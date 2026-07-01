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
    source: str
    similarity: float | None
    matched_memory_id: str | None = None
    matched_text: str | None = None

    def to_tool_message(self) -> str:
        payload = {
            "action": self.action,
            "memory_id": self.memory_id,
            "text": self.text,
            "tags": self.tags,
            "source": self.source,
            "similarity": self.similarity,
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
            payload_metadata = self._build_metadata(
                source=source,
                tags=tags,
                normalized_text=normalized_text,
                created_at=now,
                updated_at=now,
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
                source=source,
                similarity=None,
            )

        existing_metadata = duplicate.metadata
        payload_metadata = self._build_metadata(
            source=source,
            tags=tags,
            normalized_text=normalized_text,
            created_at=str(existing_metadata.get("created_at", now)),
            updated_at=now,
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
            source=source,
            similarity=duplicate.similarity,
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
        extra_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = {
            "source": source,
            "tags_csv": ",".join(tags),
            "normalized_text": normalized_text,
            "created_at": created_at,
            "updated_at": updated_at,
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

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat()
