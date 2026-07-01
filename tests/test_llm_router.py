from __future__ import annotations

import json

from config import Settings
from llm_router import HybridRouter, PreparedTurn
from memory_manager import MemorySaveResult


class FakeMemoryManager:
    def __init__(self, action: str = "inserted") -> None:
        self.saved: list[tuple[str, str]] = []
        self.action = action
        self.context = "1. [tags: tea, preference] (explicit) My favorite tea is jasmine"

    def upsert_memory(self, text: str, source: str = "manual") -> MemorySaveResult:
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

    def chat(
        self,
        messages=None,  # noqa: ANN001
        **_kwargs,  # noqa: ANN003
    ):  # noqa: D401
        self.calls += 1
        self.seen_messages.append(messages)
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
    assert tool_payload["action"] == "inserted"
    assert tool_payload["text"] == "My dentist appointment is on Friday at 2 PM."
