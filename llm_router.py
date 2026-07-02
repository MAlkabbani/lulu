from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from config import DEFAULT_SYSTEM_PROMPT, Settings
from memory_manager import MemoryHit, MemoryManager
from ollama_client import OllamaClient


SAVE_TO_MEMORY_PARAMETERS = {
    "type": "object",
    "properties": {
        "fact": {
            "type": "string",
            "description": "The durable fact to store in long-term memory.",
        }
    },
    "required": ["fact"],
    "additionalProperties": False,
}

ToolValidator = Callable[[dict[str, Any]], dict[str, Any]]
ToolExecutor = Callable[[dict[str, Any]], "ToolInvocationResult"]


class ToolCallError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolInvocationResult:
    result: dict[str, Any]
    saved_item: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    validator: ToolValidator
    executor: ToolExecutor

    def as_ollama_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolCallOutcome:
    tool_name: str
    content: str
    saved_item: str | None = None


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def definitions(self) -> list[dict[str, Any]]:
        return [tool.as_ollama_tool() for tool in self._tools.values()]

    def execute(self, tool_call: dict[str, Any]) -> ToolCallOutcome:
        function_payload = tool_call.get("function")
        if not isinstance(function_payload, dict):
            return self._error_outcome(
                "unknown_tool",
                "malformed_tool_call",
                "Tool call payload must include a function object.",
            )

        tool_name = function_payload.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return self._error_outcome(
                "unknown_tool",
                "malformed_tool_call",
                "Tool call payload must include a function name.",
            )

        tool_name = tool_name.strip()
        tool = self._tools.get(tool_name)
        if tool is None:
            return self._error_outcome(
                tool_name,
                "unsupported_tool",
                "Rejected unsupported tool request.",
            )

        try:
            arguments = self._parse_arguments(function_payload.get("arguments"))
            schema_validated = self._validate_schema(arguments, tool.parameters)
            validated_arguments = tool.validator(schema_validated)
        except ToolCallError as exc:
            return self._error_outcome(tool_name, exc.code, str(exc))

        try:
            invocation = tool.executor(validated_arguments)
        except Exception:
            return self._error_outcome(
                tool_name,
                "tool_execution_failed",
                "Tool execution failed safely in the backend.",
            )

        return ToolCallOutcome(
            tool_name=tool_name,
            content=json.dumps(
                {
                    "ok": True,
                    "tool_name": tool_name,
                    "result": invocation.result,
                },
                ensure_ascii=True,
            ),
            saved_item=invocation.saved_item,
        )

    @staticmethod
    def _error_outcome(tool_name: str, code: str, message: str) -> ToolCallOutcome:
        return ToolCallOutcome(
            tool_name=tool_name,
            content=json.dumps(
                {
                    "ok": False,
                    "tool_name": tool_name,
                    "error": {
                        "code": code,
                        "message": message,
                    },
                },
                ensure_ascii=True,
            ),
        )

    @staticmethod
    def _parse_arguments(arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ToolCallError(
                    "invalid_arguments",
                    "Tool arguments must be valid JSON.",
                ) from exc
            if isinstance(parsed, dict):
                return parsed
        raise ToolCallError("invalid_arguments", "Tool arguments must be a JSON object.")

    @staticmethod
    def _validate_schema(arguments: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        if schema.get("type") != "object":
            raise ToolCallError("invalid_schema", "Tool schema must describe an object payload.")
        if not isinstance(arguments, dict):
            raise ToolCallError("invalid_arguments", "Tool arguments must be a JSON object.")

        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in arguments:
                raise ToolCallError(
                    "missing_required_argument",
                    f"Missing required argument: {key}.",
                )

        if schema.get("additionalProperties") is False:
            unexpected_keys = sorted(set(arguments).difference(properties))
            if unexpected_keys:
                unexpected = ", ".join(unexpected_keys)
                raise ToolCallError(
                    "unexpected_argument",
                    f"Unexpected tool arguments: {unexpected}.",
                )

        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        for key, value in arguments.items():
            property_schema = properties.get(key) or {}
            expected_type = property_schema.get("type")
            if expected_type is None:
                continue
            expected_python_type = type_map.get(expected_type)
            if expected_python_type is None:
                continue
            if expected_type == "number" and isinstance(value, bool):
                raise ToolCallError(
                    "invalid_argument_type",
                    f"Argument {key} must be a {expected_type}.",
                )
            if not isinstance(value, expected_python_type):
                raise ToolCallError(
                    "invalid_argument_type",
                    f"Argument {key} must be a {expected_type}.",
                )

        return arguments


@dataclass(frozen=True)
class RouteResult:
    reply_text: str
    memory_hits: list[MemoryHit]
    saved_items: list[str]
    bypassed_llm: bool = False


@dataclass(frozen=True)
class PreparedTurn:
    fixed_reply: str = ""
    final_messages: list[dict[str, Any]] = field(default_factory=list)
    memory_hits: list[MemoryHit] = field(default_factory=list)
    saved_items: list[str] = field(default_factory=list)
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
        self.tool_registry = ToolRegistry(
            [
                ToolDefinition(
                    name="save_to_memory",
                    description="Persist a durable user fact, preference, or schedule detail.",
                    parameters=SAVE_TO_MEMORY_PARAMETERS,
                    validator=self._validate_save_to_memory_arguments,
                    executor=self._execute_save_to_memory,
                )
            ]
        )

    def handle_transcript(self, transcript: str) -> RouteResult:
        prepared = self.prepare_turn(transcript)
        if not prepared.final_messages:
            return RouteResult(
                reply_text=prepared.fixed_reply,
                memory_hits=prepared.memory_hits,
                saved_items=prepared.saved_items,
                bypassed_llm=prepared.bypassed_llm,
            )

        final_response = self.ollama_client.chat(messages=prepared.final_messages)
        final_text = ((final_response.get("message") or {}).get("content") or "").strip()
        return RouteResult(
            reply_text=final_text,
            memory_hits=prepared.memory_hits,
            saved_items=prepared.saved_items,
            bypassed_llm=prepared.bypassed_llm,
        )

    def prepare_turn(self, transcript: str) -> PreparedTurn:
        normalized = transcript.strip()
        if not normalized:
            return PreparedTurn(
                fixed_reply="",
                memory_hits=[],
                saved_items=[],
                bypassed_llm=True,
            )

        explicit_prefix = "insert info"
        if normalized.lower().startswith(explicit_prefix):
            payload = normalized[len(explicit_prefix) :].strip(" :,-")
            if not payload:
                return PreparedTurn(
                    fixed_reply="Please say what you want me to save after insert info.",
                    memory_hits=[],
                    saved_items=[],
                    bypassed_llm=True,
                )

            save_result = self.memory_manager.upsert_memory(payload, source="explicit")
            reply_text = "Information explicitly saved to vault."
            if save_result.action == "updated":
                reply_text = "Information explicitly updated in vault."
            return PreparedTurn(
                fixed_reply=reply_text,
                memory_hits=[],
                saved_items=[payload],
                bypassed_llm=True,
            )

        memory_hits = self.memory_manager.query_memory(normalized)
        messages = self._build_messages(normalized, memory_hits)
        initial_response = self.ollama_client.chat(
            messages=messages,
            tools=self.tool_registry.definitions(),
        )
        assistant_message = initial_response.get("message") or {}
        tool_calls = self.ollama_client.normalize_tool_calls(assistant_message)

        if not tool_calls:
            return PreparedTurn(
                fixed_reply="",
                final_messages=messages,
                memory_hits=memory_hits,
                saved_items=[],
            )

        selected_tool_calls = tool_calls[:1]
        assistant_tool_message = {"role": "assistant", "tool_calls": selected_tool_calls}
        tool_messages, saved_items = self._execute_tool_calls(selected_tool_calls)
        final_messages = messages + [assistant_tool_message] + tool_messages
        return PreparedTurn(
            fixed_reply="",
            final_messages=final_messages,
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

        for tool_call in tool_calls:
            outcome = self.tool_registry.execute(tool_call)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_name": outcome.tool_name,
                    "content": outcome.content,
                }
            )
            if outcome.saved_item:
                saved_items.append(outcome.saved_item)

        return tool_messages, saved_items

    def _validate_save_to_memory_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"fact": self._validate_fact(arguments.get("fact"))}

    def _execute_save_to_memory(self, arguments: dict[str, Any]) -> ToolInvocationResult:
        fact = arguments["fact"]
        save_result = self.memory_manager.upsert_memory(fact, source="tool_call")
        return ToolInvocationResult(
            result=json.loads(save_result.to_tool_message()),
            saved_item=fact,
        )

    def _validate_fact(self, fact: Any) -> str:
        if not isinstance(fact, str):
            raise ToolCallError("invalid_argument_type", "Argument fact must be a string.")

        clean_fact = fact.strip()
        if not clean_fact:
            raise ToolCallError("invalid_arguments", "Argument fact cannot be empty.")
        if len(clean_fact) > self.settings.max_fact_length:
            raise ToolCallError(
                "invalid_arguments",
                "Argument fact is too long to store safely.",
            )
        return clean_fact
