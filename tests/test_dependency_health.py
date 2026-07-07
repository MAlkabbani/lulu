from __future__ import annotations

import os
from pathlib import Path

from app_core.dependency_health import probe_dependency_health
from config import Settings
from ollama_client import OllamaClientError


class HealthyOllama:
    def healthcheck(self) -> dict[str, str]:
        return {"version": "0.3.0"}


class FailingOllama:
    def healthcheck(self) -> dict[str, str]:
        raise OllamaClientError("offline")


def test_probe_dependency_health_reports_healthy_runtime(tmp_path: Path) -> None:
    settings = Settings(chroma_path=tmp_path / "vault_db")
    settings.chroma_path.parent.mkdir(parents=True, exist_ok=True)

    health = probe_dependency_health(
        settings,
        HealthyOllama(),
        available_models=[settings.chat_model, settings.embedding_model],
    )

    assert health.ollama_reachable is True
    assert health.ollama_version == "0.3.0"
    assert health.chat_model_available is True
    assert health.embedding_model_available is True
    assert health.memory_path_available is True
    assert health.ffmpeg_available is True
    assert health.issues == []


def test_probe_dependency_health_reports_missing_dependencies(tmp_path: Path) -> None:
    settings = Settings(chroma_path=tmp_path / "missing" / "vault_db")

    health = probe_dependency_health(
        settings,
        FailingOllama(),
        available_models=[],
        audio_input_available=False,
        tts_available=False,
        ffmpeg_available=False,
    )

    assert health.ollama_reachable is False
    assert health.audio_input_available is False
    assert health.tts_available is False
    assert health.ffmpeg_available is False
    assert health.memory_path_available is False
    assert any("Unable to reach Ollama" in issue for issue in health.issues)
    assert any("Audio input is unavailable." == issue for issue in health.issues)
    assert any("macOS say is unavailable." == issue for issue in health.issues)


def test_probe_dependency_health_accepts_latest_tag_alias_for_embedding_model(
    tmp_path: Path,
) -> None:
    settings = Settings(
        chroma_path=tmp_path / "vault_db",
        embedding_model="nomic-embed-text",
    )
    settings.chroma_path.parent.mkdir(parents=True, exist_ok=True)

    health = probe_dependency_health(
        settings,
        HealthyOllama(),
        available_models=[settings.chat_model, "nomic-embed-text:latest"],
    )

    assert health.embedding_model_available is True
    assert "Missing embedding model: nomic-embed-text." not in health.issues


def test_probe_dependency_health_reports_unwritable_memory_path(tmp_path: Path) -> None:
    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    os.chmod(locked_dir, 0o500)
    settings = Settings(chroma_path=locked_dir / "vault_db")

    try:
        health = probe_dependency_health(
            settings,
            HealthyOllama(),
            available_models=[settings.chat_model, settings.embedding_model],
        )
    finally:
        os.chmod(locked_dir, 0o700)

    assert health.memory_path_available is False
    assert any("Memory path is unavailable" in issue for issue in health.issues)
