from __future__ import annotations

from pathlib import Path
from typing import Any

from app_core.runtime_models import DependencyHealth
from config import Settings
from ollama_client import OllamaClient, OllamaClientError


def probe_dependency_health(
    settings: Settings,
    ollama_client: OllamaClient,
    *,
    available_models: list[str] | None = None,
    audio_input_available: bool = True,
    tts_available: bool = True,
) -> DependencyHealth:
    issues: list[str] = []
    ollama_reachable = False
    ollama_version = "unknown"

    try:
        version_payload = ollama_client.healthcheck()
        ollama_reachable = True
        ollama_version = str(version_payload.get("version", "unknown"))
    except OllamaClientError:
        issues.append(f"Unable to reach Ollama at {settings.ollama_base_url}.")

    known_models = _known_model_aliases(available_models or [])
    chat_model_available = not known_models or _model_is_available(settings.chat_model, known_models)
    embedding_model_available = not known_models or _model_is_available(
        settings.embedding_model,
        known_models,
    )
    if ollama_reachable and known_models and not chat_model_available:
        issues.append(f"Missing chat model: {settings.chat_model}.")
    if ollama_reachable and known_models and not embedding_model_available:
        issues.append(f"Missing embedding model: {settings.embedding_model}.")

    if not audio_input_available:
        issues.append("Audio input is unavailable.")
    if not tts_available:
        issues.append("macOS say is unavailable.")

    memory_path_available = _path_is_usable(settings.chroma_path)
    if not memory_path_available:
        issues.append(f"Memory path is unavailable: {settings.chroma_path}.")

    return DependencyHealth(
        ollama_reachable=ollama_reachable,
        ollama_version=ollama_version,
        chat_model_available=chat_model_available,
        embedding_model_available=embedding_model_available,
        audio_input_available=audio_input_available,
        tts_available=tts_available,
        memory_path_available=memory_path_available,
        issues=issues,
    )


def _path_is_usable(path: Path | str) -> bool:
    candidate = Path(path)
    parent = candidate if candidate.exists() else candidate.parent
    return parent.exists() and parent.is_dir()


def _model_is_available(model_name: str, known_models: set[str]) -> bool:
    return any(alias in known_models for alias in _model_aliases(model_name))


def _known_model_aliases(model_names: list[str]) -> set[str]:
    aliases: set[str] = set()
    for model_name in model_names:
        aliases.update(_model_aliases(model_name))
    return aliases


def _model_aliases(model_name: str) -> set[str]:
    normalized = model_name.strip()
    if not normalized:
        return set()

    aliases = {normalized}
    if ":" not in normalized:
        aliases.add(f"{normalized}:latest")
        return aliases

    base_name, tag = normalized.rsplit(":", 1)
    if tag == "latest" and base_name:
        aliases.add(base_name)
    return aliases
