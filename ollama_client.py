from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests

from config import Settings


class OllamaClientError(RuntimeError):
    """Raised when local Ollama transport or payload handling fails."""


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

    def healthcheck(self) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.settings.ollama_base_url}/api/version",
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OllamaClientError(
                f"Unable to reach Ollama at {self.settings.ollama_base_url}."
            ) from exc

    def list_models(self) -> list[str]:
        try:
            response = self.session.get(
                f"{self.settings.ollama_base_url}/api/tags",
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OllamaClientError("Unable to list Ollama models.") from exc

        models = payload.get("models") or []
        names: list[str] = []
        for model in models:
            if not isinstance(model, dict):
                continue
            name = model.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        return names

    def embed_text(self, text: str) -> list[float]:
        try:
            response = self.session.post(
                f"{self.settings.ollama_base_url}/api/embed",
                json={
                    "model": self.settings.embedding_model,
                    "input": text,
                    "truncate": True,
                },
                timeout=self.settings.ollama_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OllamaClientError("Ollama embedding request failed.") from exc

        embeddings = payload.get("embeddings") or []
        if not embeddings:
            raise OllamaClientError("Ollama returned no embeddings.")
        return embeddings[0]

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.settings.chat_model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options

        try:
            response = self.session.post(
                f"{self.settings.ollama_base_url}/api/chat",
                json=payload,
                timeout=self.settings.ollama_timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OllamaClientError("Ollama chat request failed.") from exc

    def stream_chat(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        try:
            response = self.session.post(
                f"{self.settings.ollama_base_url}/api/chat",
                json={
                    "model": self.settings.chat_model,
                    "messages": messages,
                    "stream": True,
                },
                timeout=self.settings.ollama_timeout_seconds,
                stream=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaClientError("Ollama streaming request failed.") from exc

        try:
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise OllamaClientError(
                        "Ollama returned an invalid streaming payload."
                    ) from exc
                message = chunk.get("message") or {}
                content = message.get("content")
                if content:
                    yield content
        except requests.RequestException as exc:
            raise OllamaClientError("Ollama streaming connection failed mid-response.") from exc
        finally:
            response.close()

    @staticmethod
    def normalize_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            return []
        normalized: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function_payload = tool_call.get("function")
            if not isinstance(function_payload, dict):
                continue
            tool_name = function_payload.get("name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                continue
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name.strip(),
                        "arguments": function_payload.get("arguments"),
                    },
                }
            )
        return normalized

    def classify_memory_tags(self, fact: str) -> list[str]:
        response = self.chat(
            model=self.settings.memory_tag_classifier_model or self.settings.chat_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You assign 1 to 3 short lowercase tags to long-term memory facts. "
                        "Return only strict JSON in the form "
                        '{"tags":["tag-one","tag-two"]}. '
                        "Use stable noun-like tags. No prose, no markdown, no code fences."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Fact: {fact}",
                },
            ],
            options={"temperature": 0},
        )
        message = response.get("message") or {}
        content = (message.get("content") or "").strip()
        parsed = self._parse_json_content(content)

        if isinstance(parsed, dict):
            tags = parsed.get("tags")
            if isinstance(tags, list):
                return [str(tag) for tag in tags]
        if isinstance(parsed, list):
            return [str(tag) for tag in parsed]
        return []

    @staticmethod
    def _parse_json_content(content: str) -> Any:
        clean_content = content.strip()
        if clean_content.startswith("```"):
            clean_content = clean_content.strip("`")
            if clean_content.startswith("json"):
                clean_content = clean_content[4:].strip()

        try:
            return json.loads(clean_content)
        except json.JSONDecodeError:
            return None
