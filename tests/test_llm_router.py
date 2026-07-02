from __future__ import annotations

import json

from config import Settings
from llm_router import HybridRouter, PreparedTurn
from memory_manager import MemorySaveResult
from ollama_client import OllamaClient


class FakeMemoryManager:
    def __init__(self, action: str = "inserted", should_fail: bool = False) -> None:
        self.saved: list[tuple[str, str]] = []
        self.action = action
        self.should_fail = should_fail
        self.context = "1. [tags: tea, preference] (explicit) My favorite tea is jasmine"

    def upsert_memory(self, text: str, source: str = "manual") -> MemorySaveResult:
        if self.should_fail:
            raise RuntimeError("backend unavailable")
        self.saved.append((text, source))
        return MemorySaveResult(
            memory_id="memory-id",
            action=self.action,
            text=text,
            tags=["tea", "preference"],
            source=source,
            similarity=0.98 if self.action == "updated" else None,
            matched_memory_id="memory-id" if self.action == "updated" else None,
            matched_text="My favorite tea is jasmine" if self.action == "updated" else None,
        )

    def query_memory(self, _query_text: str) -> list[object]:
        return []

    def format_context(self, _hits: list[object]) -> str:
        return self.context


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
        }
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
    assert len(prepared.final_messages) == 2
    assert prepared.final_messages[1]["content"] == "What tea do I like?"


def test_prepare_turn_streams_only_post_tool_final_messages() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
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
        }
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Please remember my dentist appointment is on Friday at 2 PM.")

    assert prepared.fixed_reply == ""
    assert prepared.saved_items == ["My dentist appointment is on Friday at 2 PM."]
    assert memory.saved == [("My dentist appointment is on Friday at 2 PM.", "tool_call")]
    assert prepared.final_messages[-1]["role"] == "tool"
    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is True
    assert tool_payload["tool_name"] == "save_to_memory"
    assert tool_payload["result"]["action"] == "inserted"
    assert tool_payload["result"]["text"] == "My dentist appointment is on Friday at 2 PM."


def test_prepare_turn_returns_structured_error_for_unsupported_tool() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
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
        }
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Delete my tea preference.")

    assert prepared.saved_items == []
    assert memory.saved == []
    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is False
    assert tool_payload["tool_name"] == "delete_memory"
    assert tool_payload["error"]["code"] == "unsupported_tool"


def test_prepare_turn_returns_structured_error_for_malformed_tool_arguments() -> None:
    memory = FakeMemoryManager()
    ollama = FakeOllamaClient(
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
        }
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Please remember my tea.")

    assert prepared.saved_items == []
    assert memory.saved == []
    tool_payload = json.loads(prepared.final_messages[-1]["content"])
    assert tool_payload["ok"] is False
    assert tool_payload["tool_name"] == "save_to_memory"
    assert tool_payload["error"]["code"] == "unexpected_argument"


def test_prepare_turn_returns_structured_error_when_tool_execution_fails() -> None:
    memory = FakeMemoryManager(should_fail=True)
    ollama = FakeOllamaClient(
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
        }
    )
    router = HybridRouter(Settings(), ollama, memory)

    prepared = router.prepare_turn("Please remember my dentist appointment is on Friday at 2 PM.")

    assert prepared.saved_items == []
    assert memory.saved == []
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
