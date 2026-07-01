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
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

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

    @staticmethod
    def normalize_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            return []
        return tool_calls
