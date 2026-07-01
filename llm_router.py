from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from config import DEFAULT_SYSTEM_PROMPT, Settings
from memory_manager import MemoryHit, MemoryManager
from ollama_client import OllamaClient


SAVE_TO_MEMORY_TOOL = {
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
        },
    },
}


@dataclass(frozen=True)
class RouteResult:
    reply_text: str
    memory_hits: list[MemoryHit]
    saved_items: list[str]
    bypassed_llm: bool = False


class HybridRouter:
    def __init__(
        self,
        settings: Settings,
        ollama_client: OllamaClient,
        memory_manager: MemoryManager,
    ) -> None:
        self.settings = settings
        self.ollama_client = ollama_client
        self.memory_manager = memory_manager

    def handle_transcript(self, transcript: str) -> RouteResult:
        normalized = transcript.strip()
        if not normalized:
            return RouteResult(
                reply_text="",
                memory_hits=[],
                saved_items=[],
                bypassed_llm=True,
            )

        explicit_prefix = "insert info"
        if normalized.lower().startswith(explicit_prefix):
            payload = normalized[len(explicit_prefix) :].strip(" :,-")
            if not payload:
                return RouteResult(
                    reply_text="Please say what you want me to save after insert info.",
                    memory_hits=[],
                    saved_items=[],
                    bypassed_llm=True,
                )

            save_result = self.memory_manager.upsert_memory(payload, source="explicit")
            reply_text = "Information explicitly saved to vault."
            if save_result.action == "updated":
                reply_text = "Information explicitly updated in vault."
            return RouteResult(
                reply_text=reply_text,
                memory_hits=[],
                saved_items=[payload],
                bypassed_llm=True,
            )

        memory_hits = self.memory_manager.query_memory(normalized)
        messages = self._build_messages(normalized, memory_hits)
        initial_response = self.ollama_client.chat(
            messages=messages,
            tools=[SAVE_TO_MEMORY_TOOL],
        )
        assistant_message = initial_response.get("message") or {}
        tool_calls = self.ollama_client.normalize_tool_calls(assistant_message)

        if not tool_calls:
            return RouteResult(
                reply_text=(assistant_message.get("content") or "").strip(),
                memory_hits=memory_hits,
                saved_items=[],
            )

        assistant_tool_message = {"role": "assistant", "tool_calls": tool_calls}
        tool_messages, saved_items = self._execute_tool_calls(tool_calls)
        final_messages = messages + [assistant_tool_message] + tool_messages
        final_response = self.ollama_client.chat(messages=final_messages)
        final_text = ((final_response.get("message") or {}).get("content") or "").strip()

        return RouteResult(
            reply_text=final_text,
            memory_hits=memory_hits,
            saved_items=saved_items,
        )

    def _build_messages(
        self, user_text: str, memory_hits: list[MemoryHit]
    ) -> list[dict[str, Any]]:
        memory_context = self.memory_manager.format_context(memory_hits)
        system_prompt = (
            f"{DEFAULT_SYSTEM_PROMPT}\n"
            f"\nRetrieved memory context:\n{memory_context}\n"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]

    def _execute_tool_calls(
        self, tool_calls: list[dict[str, Any]]
    ) -> tuple[list[dict[str, str]], list[str]]:
        tool_messages: list[dict[str, str]] = []
        saved_items: list[str] = []

        for tool_call in tool_calls[:1]:
            function_payload = tool_call.get("function") or {}
            tool_name = function_payload.get("name", "")
            if tool_name != "save_to_memory":
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_name": tool_name or "unknown_tool",
                        "content": "Rejected unsupported tool request.",
                    }
                )
                continue

            try:
                arguments = self._parse_arguments(function_payload.get("arguments"))
                fact = self._validate_fact(arguments.get("fact"))
                save_result = self.memory_manager.upsert_memory(fact, source="tool_call")
                saved_items.append(fact)
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_name": "save_to_memory",
                        "content": save_result.to_tool_message(),
                    }
                )
            except Exception as exc:
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_name": "save_to_memory",
                        "content": f"Tool execution error: {exc}",
                    }
                )

        return tool_messages, saved_items

    @staticmethod
    def _parse_arguments(arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("Tool arguments must be a JSON object.")

    def _validate_fact(self, fact: Any) -> str:
        if not isinstance(fact, str):
            raise ValueError("fact must be a string.")

        clean_fact = fact.strip()
        if not clean_fact:
            raise ValueError("fact cannot be empty.")
        if len(clean_fact) > self.settings.max_fact_length:
            raise ValueError("fact is too long to store safely.")
        return clean_fact
