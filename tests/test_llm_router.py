from __future__ import annotations

from config import Settings
from llm_router import HybridRouter


class FakeMemoryManager:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []

    def upsert_memory(self, text: str, source: str = "manual") -> str:
        self.saved.append((text, source))
        return "memory-id"

    def query_memory(self, _query_text: str) -> list[object]:
        return []

    @staticmethod
    def format_context(_hits: list[object]) -> str:
        return "No relevant long-term memory was found."


class FakeOllamaClient:
    def __init__(self, response_message: dict) -> None:
        self.response_message = response_message
        self.calls = 0

    def chat(self, _messages, _tools=None):  # noqa: ANN001, D401
        self.calls += 1
        if self.calls == 1:
            return {"message": self.response_message}
        return {"message": {"role": "assistant", "content": "Saved. I will remember that."}}

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


def test_tool_call_saves_memory_and_generates_follow_up() -> None:
    memory = FakeMemoryManager()
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

    result = router.handle_transcript("Please remember my dentist appointment is on Friday at 2 PM.")

    assert result.saved_items == ["My dentist appointment is on Friday at 2 PM."]
    assert result.reply_text == "Saved. I will remember that."
    assert memory.saved == [("My dentist appointment is on Friday at 2 PM.", "tool_call")]
