from __future__ import annotations

from dataclasses import dataclass

from config import Settings
from memory_manager import MemoryManager


@dataclass
class StoredRecord:
    id: str
    document: str
    embedding: list[float]
    metadata: dict


class FakeCollection:
    def __init__(self) -> None:
        self.records: dict[str, StoredRecord] = {}

    def count(self) -> int:
        return len(self.records)

    def upsert(self, ids, documents, embeddings, metadatas) -> None:  # noqa: ANN001
        for memory_id, document, embedding, metadata in zip(
            ids, documents, embeddings, metadatas
        ):
            self.records[memory_id] = StoredRecord(
                id=memory_id,
                document=document,
                embedding=embedding,
                metadata=metadata,
            )

    def query(self, query_embeddings, n_results, include):  # noqa: ANN001
        query_embedding = query_embeddings[0]
        ranked = sorted(
            self.records.values(),
            key=lambda record: abs(query_embedding[0] - record.embedding[0]),
        )[:n_results]

        return {
            "ids": [[record.id for record in ranked]],
            "documents": [[record.document for record in ranked]],
            "distances": [
                [abs(query_embedding[0] - record.embedding[0]) for record in ranked]
            ],
            "metadatas": [[record.metadata for record in ranked]],
        }

    def get(self, ids=None, limit=None, include=None):  # noqa: ANN001
        del include
        records = list(self.records.values())
        if ids is not None:
            requested_ids = set(ids)
            records = [record for record in records if record.id in requested_ids]
        if limit is not None:
            records = records[:limit]
        return {
            "ids": [record.id for record in records],
            "documents": [record.document for record in records],
            "metadatas": [record.metadata for record in records],
        }


class FakeModelClient:
    def __init__(self, embedding_map: dict[str, list[float]], tag_map: dict[str, list[str]]):
        self.embedding_map = embedding_map
        self.tag_map = tag_map

    def embed_text(self, text: str) -> list[float]:
        return self.embedding_map[text]

    def classify_memory_tags(self, text: str) -> list[str]:
        return self.tag_map.get(text, [])


def build_settings() -> Settings:
    return Settings(
        memory_dedup_similarity_threshold=0.92,
        memory_dedup_query_k=3,
        memory_max_tags=3,
    )


def test_new_memory_insert_creates_tag_metadata() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={"My favorite tea is jasmine": [0.10]},
        tag_map={"My favorite tea is jasmine": ["tea", "preference"]},
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)

    result = manager.upsert_memory("My favorite tea is jasmine", source="explicit")

    assert result.action == "inserted"
    assert result.tags == ["tea", "preference"]
    assert result.category == "tea"
    assert result.source_label == "direct-user"
    assert result.revision_count == 1
    stored_record = collection.records[result.memory_id]
    assert stored_record.metadata["tags_csv"] == "tea,preference"
    assert stored_record.metadata["category"] == "tea"
    assert stored_record.metadata["normalized_text"] == "my favorite tea is jasmine"
    assert stored_record.metadata["source"] == "explicit"
    assert stored_record.metadata["source_label"] == "direct-user"
    assert stored_record.metadata["revision_count"] == 1
    assert stored_record.metadata["last_action"] == "inserted"


def test_near_duplicate_updates_existing_canonical_record() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={
            "My favorite tea is jasmine": [0.10],
            "My favorite tea is jasmine.": [0.12],
        },
        tag_map={
            "My favorite tea is jasmine": ["tea", "preference"],
            "My favorite tea is jasmine.": ["tea", "preference"],
        },
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)

    first = manager.upsert_memory("My favorite tea is jasmine", source="explicit")
    second = manager.upsert_memory("My favorite tea is jasmine.", source="tool_call")

    assert first.memory_id == second.memory_id
    assert second.action == "updated"
    assert second.revision_count == 2
    assert collection.count() == 1
    assert collection.records[first.memory_id].document == "My favorite tea is jasmine."
    assert collection.records[first.memory_id].metadata["previous_text"] == "My favorite tea is jasmine"


def test_conflicting_memory_uses_latest_wins_when_semantically_close() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={
            "My favorite tea is jasmine": [0.10],
            "My favorite tea is mint": [0.15],
        },
        tag_map={
            "My favorite tea is jasmine": ["tea", "preference"],
            "My favorite tea is mint": ["tea", "preference"],
        },
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)

    first = manager.upsert_memory("My favorite tea is jasmine", source="explicit")
    second = manager.upsert_memory("My favorite tea is mint", source="tool_call")

    assert second.action == "updated"
    assert first.memory_id == second.memory_id
    assert collection.count() == 1
    assert collection.records[first.memory_id].document == "My favorite tea is mint"


def test_classification_fallback_uses_general_tag() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={"I like quiet mornings": [0.25]},
        tag_map={"I like quiet mornings": []},
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)

    result = manager.upsert_memory("I like quiet mornings", source="tool_call")

    assert result.tags == ["general"]
    assert collection.records[result.memory_id].metadata["tags_csv"] == "general"


def test_format_context_includes_tags_and_source() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={
            "My dentist appointment is on Friday at 2 PM.": [0.30],
            "When is my appointment?": [0.31],
        },
        tag_map={"My dentist appointment is on Friday at 2 PM.": ["schedule", "dentist"]},
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)
    manager.upsert_memory("My dentist appointment is on Friday at 2 PM.", source="tool_call")

    hits = manager.query_memory("When is my appointment?")
    context = manager.format_context(hits)

    assert "[tags: schedule, dentist] (tool_call)" in context
    assert "My dentist appointment is on Friday at 2 PM." in context


def test_list_recent_memories_orders_by_updated_at_desc() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={
            "My favorite tea is jasmine": [0.10],
            "My dentist appointment is on Friday at 2 PM.": [0.30],
        },
        tag_map={
            "My favorite tea is jasmine": ["tea", "preference"],
            "My dentist appointment is on Friday at 2 PM.": ["schedule", "dentist"],
        },
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)
    manager.upsert_memory("My favorite tea is jasmine", source="explicit")
    second = manager.upsert_memory("My dentist appointment is on Friday at 2 PM.", source="tool_call")

    hits = manager.list_recent_memories(limit=2)

    assert [hit.id for hit in hits] == [second.memory_id, hits[1].id]
    assert hits[0].metadata["updated_at"] >= hits[1].metadata["updated_at"]


def test_explain_memory_returns_auditable_metadata() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={
            "My favorite tea is jasmine": [0.10],
            "My favorite tea is mint": [0.15],
        },
        tag_map={
            "My favorite tea is jasmine": ["tea", "preference"],
            "My favorite tea is mint": ["tea", "preference"],
        },
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)
    first = manager.upsert_memory("My favorite tea is jasmine", source="explicit")
    manager.upsert_memory("My favorite tea is mint", source="tool_call")

    explanation = manager.explain_memory(first.memory_id)

    assert explanation is not None
    assert explanation["memory_id"] == first.memory_id
    assert explanation["category"] == "tea"
    assert explanation["source_label"] == "model-mediated"
    assert explanation["revision_count"] == 2
    assert explanation["last_action"] == "updated"
    assert explanation["previous_text"] == "My favorite tea is jasmine"
    assert "Canonical memory in category tea" in explanation["explanation"]


def test_serialize_hit_includes_match_confidence_for_search_results() -> None:
    collection = FakeCollection()
    model_client = FakeModelClient(
        embedding_map={
            "My favorite tea is jasmine": [0.10],
            "What tea do I like?": [0.12],
        },
        tag_map={
            "My favorite tea is jasmine": ["tea", "preference"],
        },
    )
    manager = MemoryManager(build_settings(), model_client, collection=collection)
    manager.upsert_memory("My favorite tea is jasmine", source="explicit")

    hit = manager.query_memory("What tea do I like?")[0]
    payload = manager.serialize_hit(hit, include_similarity=True)

    assert payload["category"] == "tea"
    assert payload["source_label"] == "direct-user"
    assert payload["revision_count"] == 1
    assert payload["match_confidence"] == "exact"
