from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import chromadb

from config import Settings


@dataclass(frozen=True)
class MemoryHit:
    text: str
    distance: float | None
    metadata: dict[str, Any]


class MemoryManager:
    def __init__(self, settings: Settings, embedding_client: Any) -> None:
        self.settings = settings
        self.embedding_client = embedding_client
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
    ) -> str:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Cannot save an empty memory.")

        embedding = self.embedding_client.embed_text(clean_text)
        memory_id = str(uuid4())
        payload_metadata = {"source": source, **(metadata or {})}

        self.collection.upsert(
            ids=[memory_id],
            documents=[clean_text],
            embeddings=[embedding],
            metadatas=[payload_metadata],
        )
        return memory_id

    def query_memory(self, query_text: str, k: int | None = None) -> list[MemoryHit]:
        clean_query = query_text.strip()
        if not clean_query or self.collection.count() == 0:
            return []

        embedding = self.embedding_client.embed_text(clean_query)
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=k or self.settings.top_k_memories,
            include=["documents", "distances", "metadatas"],
        )

        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        hits: list[MemoryHit] = []
        for document, distance, metadata in zip(documents, distances, metadatas):
            hits.append(
                MemoryHit(
                    text=document,
                    distance=distance,
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
            lines.append(f"{index}. ({source}) {hit.text}")
        return "\n".join(lines)
