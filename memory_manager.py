from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any
from uuid import uuid4

import chromadb

from config import Settings


@dataclass(frozen=True)
class MemoryHit:
    id: str
    text: str
    distance: float | None
    similarity: float | None
    tags: list[str]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MemorySaveResult:
    memory_id: str
    action: str
    text: str
    tags: list[str]
    category: str
    source: str
    source_label: str
    revision_count: int
    similarity: float | None
    updated_at: str
    matched_memory_id: str | None = None
    matched_text: str | None = None

    def to_tool_message(self) -> str:
        payload = {
            "action": self.action,
            "memory_id": self.memory_id,
            "text": self.text,
            "tags": self.tags,
            "category": self.category,
            "source": self.source,
            "source_label": self.source_label,
            "revision_count": self.revision_count,
            "similarity": self.similarity,
            "updated_at": self.updated_at,
            "matched_memory_id": self.matched_memory_id,
            "matched_text": self.matched_text,
        }
        return json.dumps(payload, ensure_ascii=True)


class MemoryManager:
    def __init__(
        self,
        settings: Settings,
        model_client: Any,
        collection: Any | None = None,
    ) -> None:
        self.settings = settings
        self.model_client = model_client
        if collection is not None:
            self.client = None
            self.collection = collection
        else:
            self.client = chromadb.PersistentClient(path=str(settings.chroma_path))
            self.collection = self.client.get_or_create_collection(
                name=settings.chroma_collection,
                metadata={"hnsw:space": "cosine"},
            )

    def upsert_memory(
        self,
        text: str,
        source: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> MemorySaveResult:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Cannot save an empty memory.")

        normalized_text = self._normalize_text(clean_text)
        embedding = self.model_client.embed_text(clean_text)
        duplicate = self._find_duplicate_candidate(
            normalized_text=normalized_text,
            embedding=embedding,
        )
        tags = self._classify_tags(clean_text)
        now = self._timestamp()

        if duplicate is None:
            memory_id = str(uuid4())
            revision_count = 1
            payload_metadata = self._build_metadata(
                source=source,
                tags=tags,
                normalized_text=normalized_text,
                created_at=now,
                updated_at=now,
                revision_count=revision_count,
                last_action="inserted",
                previous_text="",
                extra_metadata=metadata,
            )
            self.collection.upsert(
                ids=[memory_id],
                documents=[clean_text],
                embeddings=[embedding],
                metadatas=[payload_metadata],
            )
            return MemorySaveResult(
                memory_id=memory_id,
                action="inserted",
                text=clean_text,
                tags=tags,
                category=self._primary_category(tags),
                source=source,
                source_label=self._source_label(source),
                revision_count=revision_count,
                similarity=None,
                updated_at=now,
            )

        existing_metadata = duplicate.metadata
        revision_count = self._revision_count(existing_metadata) + 1
        payload_metadata = self._build_metadata(
            source=source,
            tags=tags,
            normalized_text=normalized_text,
            created_at=str(existing_metadata.get("created_at", now)),
            updated_at=now,
            revision_count=revision_count,
            last_action="updated",
            previous_text=duplicate.text,
            extra_metadata=metadata,
        )
        self.collection.upsert(
            ids=[duplicate.id],
            documents=[clean_text],
            embeddings=[embedding],
            metadatas=[payload_metadata],
        )
        return MemorySaveResult(
            memory_id=duplicate.id,
            action="updated",
            text=clean_text,
            tags=tags,
            category=self._primary_category(tags),
            source=source,
            source_label=self._source_label(source),
            revision_count=revision_count,
            similarity=duplicate.similarity,
            updated_at=now,
            matched_memory_id=duplicate.id,
            matched_text=duplicate.text,
        )

    def query_memory(self, query_text: str, k: int | None = None) -> list[MemoryHit]:
        clean_query = query_text.strip()
        if not clean_query or self.collection.count() == 0:
            return []

        embedding = self.model_client.embed_text(clean_query)
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=k or self.settings.top_k_memories,
            include=["documents", "distances", "metadatas"],
        )

        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        hits: list[MemoryHit] = []
        for memory_id, document, distance, metadata in zip(
            ids, documents, distances, metadatas
        ):
            tags = self._parse_tags(metadata)
            hits.append(
                MemoryHit(
                    id=memory_id,
                    text=document,
                    distance=distance,
                    similarity=self._similarity_from_distance(distance),
                    tags=tags,
                    metadata=metadata or {},
                )
            )
        return hits

    def list_recent_memories(self, limit: int) -> list[MemoryHit]:
        if limit < 1 or self.collection.count() == 0:
            return []

        raw = self.collection.get(
            limit=limit,
            include=["documents", "metadatas"],
        )
        hits = self._hits_from_get_result(raw)
        hits.sort(
            key=lambda hit: str(hit.metadata.get("updated_at", "")),
            reverse=True,
        )
        return hits[:limit]

    def get_memory(self, memory_id: str) -> MemoryHit | None:
        clean_memory_id = memory_id.strip()
        if not clean_memory_id:
            return None

        raw = self.collection.get(
            ids=[clean_memory_id],
            include=["documents", "metadatas"],
        )
        hits = self._hits_from_get_result(raw)
        if not hits:
            return None
        return hits[0]

    def explain_memory(self, memory_id: str) -> dict[str, Any] | None:
        hit = self.get_memory(memory_id)
        if hit is None:
            return None

        metadata = hit.metadata or {}
        revision_count = self._revision_count(metadata)
        category = self._primary_category(hit.tags)
        explanation = (
            f"Canonical memory in category {category}, last captured via "
            f"{self._source_label(str(metadata.get('source', 'unknown')))}, "
            f"revised {revision_count} time(s), and updated at "
            f"{metadata.get('updated_at', 'unknown')}."
        )
        previous_text = str(metadata.get("previous_text", "")).strip()
        return {
            "memory_id": hit.id,
            "text": hit.text,
            "tags": hit.tags,
            "category": category,
            "source": metadata.get("source", "unknown"),
            "source_label": self._source_label(str(metadata.get("source", "unknown"))),
            "revision_count": revision_count,
            "last_action": metadata.get("last_action", "unknown"),
            "created_at": metadata.get("created_at", "unknown"),
            "updated_at": metadata.get("updated_at", "unknown"),
            "previous_text": previous_text or None,
            "explanation": explanation,
        }

    @staticmethod
    def format_context(hits: list[MemoryHit]) -> str:
        if not hits:
            return "No relevant long-term memory was found."

        lines = []
        for index, hit in enumerate(hits, start=1):
            source = hit.metadata.get("source", "unknown")
            tags = ", ".join(hit.tags) if hit.tags else "general"
            lines.append(f"{index}. [tags: {tags}] ({source}) {hit.text}")
        return "\n".join(lines)

    def serialize_hit(
        self,
        hit: MemoryHit,
        *,
        include_similarity: bool,
    ) -> dict[str, Any]:
        metadata = hit.metadata or {}
        payload = {
            "memory_id": hit.id,
            "text": hit.text,
            "tags": hit.tags,
            "category": self._primary_category(hit.tags),
            "source": metadata.get("source", "unknown"),
            "source_label": self._source_label(str(metadata.get("source", "unknown"))),
            "revision_count": self._revision_count(metadata),
            "updated_at": metadata.get("updated_at", "unknown"),
        }
        if include_similarity:
            payload["similarity"] = hit.similarity
            payload["match_confidence"] = self._match_confidence_label(hit.similarity)
        return payload

    def _find_duplicate_candidate(
        self,
        normalized_text: str,
        embedding: list[float],
    ) -> MemoryHit | None:
        if self.collection.count() == 0:
            return None

        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=self.settings.memory_dedup_query_k,
            include=["documents", "distances", "metadatas"],
        )
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        best_match: MemoryHit | None = None
        for memory_id, document, distance, metadata in zip(
            ids, documents, distances, metadatas
        ):
            metadata = metadata or {}
            existing_normalized = str(metadata.get("normalized_text", ""))
            similarity = self._similarity_from_distance(distance)
            hit = MemoryHit(
                id=memory_id,
                text=document,
                distance=distance,
                similarity=similarity,
                tags=self._parse_tags(metadata),
                metadata=metadata,
            )

            if existing_normalized and existing_normalized == normalized_text:
                return hit

            if (
                similarity is not None
                and similarity >= self.settings.memory_dedup_similarity_threshold
            ):
                if best_match is None or similarity > (best_match.similarity or 0.0):
                    best_match = hit

        return best_match

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    def _classify_tags(self, text: str) -> list[str]:
        raw_tags = self.model_client.classify_memory_tags(text)
        normalized_tags: list[str] = []

        for raw_tag in raw_tags:
            candidate = self._normalize_tag(raw_tag)
            if not candidate or candidate in normalized_tags:
                continue
            normalized_tags.append(candidate)
            if len(normalized_tags) >= self.settings.memory_max_tags:
                break

        if normalized_tags:
            return normalized_tags
        return ["general"]

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        clean_tag = tag.strip().lower().replace("_", "-")
        clean_tag = re.sub(r"[^a-z0-9 -]", "", clean_tag)
        clean_tag = re.sub(r"\s+", "-", clean_tag).strip("-")
        return clean_tag

    def _build_metadata(
        self,
        source: str,
        tags: list[str],
        normalized_text: str,
        created_at: str,
        updated_at: str,
        revision_count: int,
        last_action: str,
        previous_text: str,
        extra_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = {
            "source": source,
            "source_label": self._source_label(source),
            "tags_csv": ",".join(tags),
            "category": self._primary_category(tags),
            "normalized_text": normalized_text,
            "created_at": created_at,
            "updated_at": updated_at,
            "revision_count": revision_count,
            "last_action": last_action,
            "previous_text": previous_text,
            "memory_kind": "canonical",
        }
        if extra_metadata:
            for key, value in extra_metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    payload[key] = value
        return payload

    @staticmethod
    def _parse_tags(metadata: dict[str, Any] | None) -> list[str]:
        if not metadata:
            return []
        raw_tags = str(metadata.get("tags_csv", "")).strip()
        if not raw_tags:
            return []
        return [tag for tag in raw_tags.split(",") if tag]

    @staticmethod
    def _similarity_from_distance(distance: float | None) -> float | None:
        if distance is None:
            return None
        similarity = 1.0 - float(distance)
        return max(0.0, min(1.0, similarity))

    def _hits_from_get_result(self, raw: dict[str, Any]) -> list[MemoryHit]:
        ids = raw.get("ids", [])
        documents = raw.get("documents", [])
        metadatas = raw.get("metadatas", [])

        if ids and isinstance(ids[0], list):
            ids = ids[0]
        if documents and isinstance(documents[0], list):
            documents = documents[0]
        if metadatas and isinstance(metadatas[0], list):
            metadatas = metadatas[0]

        hits: list[MemoryHit] = []
        for memory_id, document, metadata in zip(ids, documents, metadatas):
            metadata = metadata or {}
            hits.append(
                MemoryHit(
                    id=memory_id,
                    text=document,
                    distance=None,
                    similarity=None,
                    tags=self._parse_tags(metadata),
                    metadata=metadata,
                )
            )
        return hits

    @staticmethod
    def _primary_category(tags: list[str]) -> str:
        if tags:
            return tags[0]
        return "general"

    @staticmethod
    def _source_label(source: str) -> str:
        if source == "explicit":
            return "direct-user"
        if source == "tool_call":
            return "model-mediated"
        if source == "manual":
            return "manual"
        if not source:
            return "unknown"
        return source

    @staticmethod
    def _revision_count(metadata: dict[str, Any]) -> int:
        raw_value = metadata.get("revision_count", 1)
        if isinstance(raw_value, bool):
            return 1
        if isinstance(raw_value, int):
            return max(raw_value, 1)
        try:
            return max(int(str(raw_value)), 1)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _match_confidence_label(similarity: float | None) -> str:
        if similarity is None:
            return "n/a"
        if similarity >= 0.97:
            return "exact"
        if similarity >= 0.90:
            return "high"
        if similarity >= 0.80:
            return "medium"
        return "low"

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()
