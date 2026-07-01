from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests

from config import Settings


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

    def healthcheck(self) -> dict[str, Any]:
        response = self.session.get(
            f"{self.settings.ollama_base_url}/api/version",
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def embed_text(self, text: str) -> list[float]:
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
        embeddings = payload.get("embeddings") or []
        if not embeddings:
            raise RuntimeError("Ollama returned no embeddings.")
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

        response = self.session.post(
            f"{self.settings.ollama_base_url}/api/chat",
            json=payload,
            timeout=self.settings.ollama_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def stream_chat(self, messages: list[dict[str, Any]]) -> Iterator[str]:
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

        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            chunk = json.loads(raw_line.decode("utf-8"))
            message = chunk.get("message") or {}
            content = message.get("content")
            if content:
                yield content
        response.close()

    @staticmethod
    def normalize_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            return []
        return tool_calls

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
