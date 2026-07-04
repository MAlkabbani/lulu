from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DependencyHealth:
    ollama_reachable: bool
    ollama_version: str = "unknown"
    chat_model_available: bool = False
    embedding_model_available: bool = False
    audio_input_available: bool = True
    tts_available: bool = True
    memory_path_available: bool = True
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeSnapshot:
    mode: str = "starting"
    runtime_mode: str = "continuous"
    status_line: str = "Booting Lulu..."
    degraded: bool = False
    last_error: str = ""


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    payload: dict[str, Any]


def make_event(event_type: str, **payload: Any) -> RuntimeEvent:
    return RuntimeEvent(event_type=event_type, payload=payload)

