from __future__ import annotations

import json

from config import Settings
from llm_router import HybridRouter, PreparedTurn
from memory_manager import MemoryHit, MemorySaveResult
from ollama_client import OllamaClient


class FakeMemoryManager:
    def __init__(self, action: str = "inserted", should_fail: bool = False) -> None:
        self.saved: list[tuple[str, str]] = []
        self.queries: list[tuple[str, int | None]] = []
        self.recent_limits: list[int] = []
        self.explained_ids: list[str] = []
        self.action = action
        self.should_fail = should_fail
        self.context = "1. [tags: tea, preference] (explicit) My favorite tea is jasmine"
        self.search_hits: list[MemoryHit] = [
            MemoryHit(
                id="memory-id",
                text="My favorite tea is jasmine",
                distance=0.08,
                similarity=0.92,
                tags=["tea", "preference"],
                metadata={
                    "source": "explicit",
                    "source_label": "direct-user",
                    "updated_at": "2026-07-02T00:00:00+00:00",
                    "revision_count": 1,
                    "last_action": "inserted",
                    "category": "tea",
                },
            )
        ]

    def upsert_memory(self, text: str, source: str = "manual") -> MemorySaveResult:
        if self.should_fail:
            raise RuntimeError("backend unavailable")
        self.saved.append((text, source))
        return MemorySaveResult(
            memory_id="memory-id",
            action=self.action,
            text=text,
            tags=["tea", "preference"],
            category="tea",
            source=source,
            source_label="model-mediated" if source == "tool_call" else "direct-user",
            revision_count=2 if self.action == "updated" else 1,
            similarity=0.98 if self.action == "updated" else None,
            updated_at="2026-07-02T00:00:00+00:00",
            matched_memory_id="memory-id" if self.action == "updated" else None,
            matched_text="My favorite tea is jasmine" if self.action == "updated" else None,
        )

    def query_memory(self, query_text: str, k: int | None = None) -> list[object]:
        self.queries.append((query_text, k))
        if k is None:
            return []
        return self.search_hits[:k]

    def format_context(self, _hits: list[object]) -> str:
        return self.context

    def list_recent_memories(self, limit: int) -> list[MemoryHit]:
        self.recent_limits.append(limit)
        return self.search_hits[:limit]

    def explain_memory(self, memory_id: str) -> dict | None:
        self.explained_ids.append(memory_id)
        if memory_id != "memory-id":
            return None
        return {
            "memory_id": memory_id,
            "text": "My favorite tea is jasmine",
            "tags": ["tea", "preference"],
            "category": "tea",
            "source": "explicit",
            "source_label": "direct-user",
            "revision_count": 1,
            "last_action": "inserted",
            "created_at": "2026-07-01T00:00:00+00:00",
            "updated_at": "2026-07-02T00:00:00+00:00",
            "previous_text": None,
            "explanation": (
                "Canonical memory in category tea, last captured via direct-user, "
                "revised 1 time(s), and updated at 2026-07-02T00:00:00+00:00."
            ),
        }

    def serialize_hit(self, hit: MemoryHit, *, include_similarity: bool) -> dict:
        payload = {
            "memory_id": hit.id,
            "text": hit.text,
            "tags": hit.tags,
            "category": "tea",
            "source": hit.metadata.get("source", "unknown"),
            "source_label": hit.metadata.get("source_label", "unknown"),
            "revision_count": hit.metadata.get("revision_count", 1),
            "updated_at": hit.metadata.get("updated_at", "unknown"),
        }
        if include_similarity:
            payload["similarity"] = hit.similarity
            payload["match_confidence"] = "high"
        return payload


class FakeOllamaClient:
    def __init__(self, response_messages: list[dict] | dict) -> None:
        if isinstance(response_messages, list):
            self.response_messages = response_messages
        else:
            self.response_messages = [response_messages]
        self.calls = 0
        self.seen_messages: list[list[dict]] = []
        self.seen_tools: list[list[dict] | None] = []

    def chat(
        self,
        messages=None,  # noqa: ANN001
        **kwargs,  # noqa: ANN003
    ):  # noqa: D401
        self.calls += 1
        self.seen_messages.append(messages)
        self.seen_tools.append(kwargs.get("tools"))
        index = min(self.calls - 1, len(self.response_messages) - 1)
        return {"message": self.response_messages[index]}

    @staticmethod
    def normalize_tool_calls(message):
        return message.get("tool_calls") or []


def test_explicit_insert_info_bypasses_llm() -> None:
    memory = FakeMemoryManager()
    router = HybridRouter(Settings(), FakeOllamaClient({"content": "unused"}), memory)

    result = router.handle_transcript("insert info my favorite tea is jasmine")

    assert result.bypassed_llm is True
    assert result.saved_items == ["my favorite tea is jasmine"]
    assert result.invocation_path == "explicit_save"
    assert result.invocation_summary == "Deterministic memory save via insert info."
    assert memory.saved == [("my favorite tea is jasmine", "explicit")]


def test_explicit_insert_info_reports_update_when_duplicate_is_merged() -> None:
    memory = FakeMemoryManager(action="updated")
    router = HybridRouter(Settings(), FakeOllamaClient({"content": "unused"}), memory)

    result = router.handle_transcript("insert info my favorite tea is mint")

    assert result.reply_text == "Information explicitly updated in vault."
    assert memory.saved == [("my favorite tea is mint", "explicit")]


def test_tool_call_saves_memory_and_generates_follow_up() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "save_to_memory",
                            "arguments": {"fact": "My dentist appointment is on Friday at 2 PM."},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "Saved. I will remember that."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    result = router.handle_transcript("Please remember my dentist appointment is on Friday at 2 PM.")

    assert result.saved_items == ["My dentist appointment is on Friday at 2 PM."]
    assert result.reply_text == "Saved. I will remember that."
    assert result.invocation_path == "model_tool_call"
    assert "Natural-language backend action succeeded" in result.invocation_summary
    assert memory.saved == [("My dentist appointment is on Friday at 2 PM.", "tool_call")]
    assert ollama.seen_tools[0] == [
        {
            "type": "function",
            "function": {
                "name": "save_to_memory",
                "description": "Persist a durable user fact, preference, or schedule detail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The durable fact to store in long-term memory.",
                        }
                    },
                    "required": ["fact"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": "Inspect remembered facts relevant to a topic or question.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The natural-language memory topic or fact to look up.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Optional maximum number of memory hits to return.",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_recent_memories",
                "description": "List the most recently updated canonical memories.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Optional maximum number of recent memories to return.",
                        }
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "explain_memory_hit",
                "description": "Explain a specific memory returned by search or recent-memory lookup.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {
                            "type": "string",
                            "description": "The memory identifier returned by a prior search or recent-memory list.",
                        }
                    },
                    "required": ["memory_id"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def test_router_includes_tag_aware_context_in_system_prompt() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {"role": "assistant", "content": "You like jasmine tea."},
            {"role": "assistant", "content": "You like jasmine tea."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    result = router.handle_transcript("What tea do I like?")

    assert result.reply_text == "You like jasmine tea."
    assert "[tags: tea, preference] (explicit) My favorite tea is jasmine" in ollama.seen_messages[0][0]["content"]


def test_prepare_turn_returns_streamable_messages_for_non_tool_reply() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient({"role": "assistant", "content": "You like jasmine tea."})
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("What tea do I like?")

    assert isinstance(prepared, PreparedTurn)
    assert prepared.fixed_reply == ""
    assert prepared.saved_items == []
    assert prepared.invocation_path == "chat_only"
    assert prepared.invocation_summary == "Normal chat reply; no backend action requested."
    assert len(prepared.final_messages) == 2
    assert prepared.final_messages[1]["content"] == "What tea do I like?"


def test_prepare_turn_streams_only_post_tool_final_messages() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "save_to_memory",
                            "arguments": json.dumps(
                                {"fact": "My dentist appointment is on Friday at 2 PM."}
                            ),
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "Saved. I will remember that."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Please remember my dentist appointment is on Friday at 2 PM.")

    assert prepared.fixed_reply == ""
    assert prepared.saved_items == ["My dentist appointment is on Friday at 2 PM."]
    assert memory.saved == [("My dentist appointment is on Friday at 2 PM.", "tool_call")]
    assert prepared.invocation_path == "model_tool_call"
    assert "Natural-language backend action succeeded" in prepared.invocation_summary
    assert [trace.stage for trace in prepared.tool_traces] == ["selected", "running", "succeeded"]
    assert prepared.final_messages[-1]["role"] == "tool"
    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is True
    assert tool_payload["tool_name"] == "save_to_memory"
    assert tool_payload["result"]["action"] == "inserted"
    assert tool_payload["result"]["text"] == "My dentist appointment is on Friday at 2 PM."


def test_prepare_turn_supports_multiple_tools_in_order() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": {"query": "tea preference", "limit": 1},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "save_to_memory",
                            "arguments": {"fact": "My favorite tea is jasmine."},
                        },
                    },
                ],
            },
            {"role": "assistant", "content": "You like jasmine tea, and I saved the updated phrasing."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Check what tea I like and remember the phrasing.")

    assert prepared.invocation_path == "model_tool_call"
    assert prepared.saved_items == ["My favorite tea is jasmine."]
    assert memory.queries[-1] == ("tea preference", 1)
    assert memory.saved == [("My favorite tea is jasmine.", "tool_call")]
    tool_names = [message["tool_name"] for message in prepared.final_messages if message["role"] == "tool"]
    assert tool_names == ["search_memory", "save_to_memory"]
    assert [trace.stage for trace in prepared.tool_traces] == [
        "selected",
        "running",
        "succeeded",
        "selected",
        "running",
        "succeeded",
    ]


def test_prepare_turn_supports_recent_memory_listing() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "list_recent_memories",
                            "arguments": {"limit": 1},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "Your latest memory is about jasmine tea."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    result = router.handle_transcript("What did you remember most recently?")

    assert result.reply_text == "Your latest memory is about jasmine tea."
    assert memory.recent_limits == [1]
    tool_messages = [message for message in ollama.seen_messages[-1] if message["role"] == "tool"]
    payload = json.loads(tool_messages[0]["content"])
    assert payload["ok"] is True
    assert payload["tool_name"] == "list_recent_memories"
    assert payload["result"]["hit_count"] == 1
    assert payload["result"]["hits"][0]["category"] == "tea"


def test_prepare_turn_supports_explaining_memory_hit() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "explain_memory_hit",
                            "arguments": {"memory_id": "memory-id"},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "That memory is a direct user preference."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    result = router.handle_transcript("Explain that memory entry.")

    assert result.reply_text == "That memory is a direct user preference."
    assert memory.explained_ids == ["memory-id"]
    tool_messages = [message for message in ollama.seen_messages[-1] if message["role"] == "tool"]
    payload = json.loads(tool_messages[0]["content"])
    assert payload["ok"] is True
    assert payload["tool_name"] == "explain_memory_hit"
    assert payload["result"]["source_label"] == "direct-user"
    assert payload["result"]["revision_count"] == 1


def test_prepare_turn_bounds_tool_rounds() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": {"query": "tea"},
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": {"query": "tea"},
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": {"query": "tea"},
                        },
                    }
                ],
            },
        ]
    )
    router = HybridRouter(Settings(tool_max_rounds=2), ollama, memory)

    prepared = router.prepare_turn("Keep checking my tea memories.")

    assert ollama.calls == 2
    assert prepared.invocation_path == "model_tool_call"
    assert "Stopped backend tool execution after 2 round(s)." in prepared.invocation_summary
    assert prepared.tool_traces[-1].stage == "limit_reached"
    assert len([trace for trace in prepared.tool_traces if trace.stage == "succeeded"]) == 2


def test_handle_transcript_hands_tool_results_into_final_generation() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": {"query": "tea preference", "limit": 1},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "save_to_memory",
                            "arguments": {"fact": "My favorite tea is jasmine."},
                        },
                    },
                ],
            },
            {"role": "assistant", "content": "I found your tea preference and refreshed it."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    result = router.handle_transcript("Check my tea memory and save the latest phrasing.")

    assert result.reply_text == "I found your tea preference and refreshed it."
    tool_messages = [message for message in ollama.seen_messages[-1] if message["role"] == "tool"]
    assert [message["tool_name"] for message in tool_messages] == ["search_memory", "save_to_memory"]
    first_tool_payload = json.loads(tool_messages[0]["content"])
    second_tool_payload = json.loads(tool_messages[1]["content"])
    assert first_tool_payload["result"]["hit_count"] == 1
    assert second_tool_payload["result"]["action"] == "inserted"


def test_prepare_turn_allows_partial_tool_failure_without_dropping_other_results() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "delete_memory",
                            "arguments": {"query": "tea"},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": {"query": "tea preference", "limit": 1},
                        },
                    },
                ],
            },
            {"role": "assistant", "content": "I searched memory and skipped the unsupported delete."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Delete anything stale, then inspect my tea memories.")

    assert prepared.invocation_path == "model_tool_call"
    assert "partially succeeded" in prepared.invocation_summary
    tool_payloads = [
        json.loads(message["content"]) for message in prepared.final_messages if message["role"] == "tool"
    ]
    assert tool_payloads[0]["ok"] is False
    assert tool_payloads[0]["error"]["code"] == "unsupported_tool"
    assert tool_payloads[1]["ok"] is True
    assert tool_payloads[1]["tool_name"] == "search_memory"


def test_prepare_turn_returns_structured_error_for_unsupported_tool() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "delete_memory",
                            "arguments": {},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "I cannot delete memories from the current tool surface."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Delete my tea preference.")

    assert prepared.saved_items == []
    assert memory.saved == []
    assert prepared.invocation_path == "model_tool_call"
    assert "rejected safely" in prepared.invocation_summary
    assert [trace.stage for trace in prepared.tool_traces] == ["selected", "running", "failed"]
    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is False
    assert tool_payload["tool_name"] == "delete_memory"
    assert tool_payload["error"]["code"] == "unsupported_tool"


def test_prepare_turn_returns_structured_error_for_malformed_tool_arguments() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "save_to_memory",
                            "arguments": {"fact": "tea", "extra": "nope"},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "I could not save that because the tool call was malformed."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Please remember my tea.")

    assert prepared.saved_items == []
    assert memory.saved == []
    assert prepared.invocation_path == "model_tool_call"
    assert "rejected safely" in prepared.invocation_summary
    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is False
    assert tool_payload["tool_name"] == "save_to_memory"
    assert tool_payload["error"]["code"] == "unexpected_argument"


def test_prepare_turn_returns_structured_error_when_tool_execution_fails() -> None:
    memory = FakeMemoryManager(should_fail=True)
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "save_to_memory",
                            "arguments": {"fact": "My dentist appointment is on Friday at 2 PM."},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "I could not save that safely right now."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Please remember my dentist appointment is on Friday at 2 PM.")

    assert prepared.saved_items == []
    assert memory.saved == []
    assert prepared.invocation_path == "model_tool_call"
    assert "rejected safely" in prepared.invocation_summary
    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is False
    assert tool_payload["tool_name"] == "save_to_memory"
    assert tool_payload["error"]["code"] == "tool_execution_failed"


def test_normalize_tool_calls_filters_malformed_entries() -> None:
    tool_calls = OllamaClient.normalize_tool_calls(
        {
            "tool_calls": [
                "bad-entry",
                {"type": "function"},
                {"function": {"name": "", "arguments": {}}},
                {"function": {"name": " save_to_memory ", "arguments": {"fact": "tea"}}},
            ]
        }
    )

    assert tool_calls == [
        {
            "type": "function",
            "function": {
                "name": "save_to_memory",
                "arguments": {"fact": "tea"},
            },
        }
    ]


def test_prepare_turn_rejects_boolean_search_limit() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "search_memory",
                            "arguments": {"query": "tea", "limit": True},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "I could not inspect memory with that invalid limit."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Search my tea memories.")

    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is False
    assert tool_payload["error"]["code"] == "invalid_argument_type"


def test_prepare_turn_returns_error_when_explaining_missing_memory_id() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "explain_memory_hit",
                            "arguments": {"memory_id": "missing-id"},
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "I could not find that memory entry."},
        ]
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Explain the missing memory entry.")

    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is False
    assert tool_payload["tool_name"] == "explain_memory_hit"
    assert tool_payload["error"]["code"] == "memory_not_found"
