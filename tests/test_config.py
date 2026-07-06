from __future__ import annotations

from pathlib import Path

import pytest

from config import Settings


def test_settings_reads_environment_at_instantiation_time(monkeypatch) -> None:
    monkeypatch.setenv("LULU_APP_NAME", "Lulu OSS")
    monkeypatch.setenv("TOP_K_MEMORIES", "4")

    settings = Settings()

    assert settings.app_name == "Lulu OSS"
    assert settings.top_k_memories == 4


def test_settings_reports_invalid_numeric_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("TOP_K_MEMORIES", "four")

    with pytest.raises(ValueError, match="TOP_K_MEMORIES"):
        Settings()


def test_settings_reports_invalid_boolean_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("PRACTICAL_VOICE_MODE", "sometimes")

    with pytest.raises(ValueError, match="PRACTICAL_VOICE_MODE"):
        Settings()


def test_settings_rejects_blank_wake_phrase(monkeypatch) -> None:
    monkeypatch.setenv("WAKE_PHRASE", "   ")

    with pytest.raises(ValueError, match="WAKE_PHRASE"):
        Settings()


def test_settings_reads_config_values_from_explicit_config_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CHROMA_PATH", raising=False)
    config_path = tmp_path / "settings.json"
    config_path.write_text(
        '{"CHROMA_PATH": "/tmp/from-config", "wake_phrase": "hello lulu"}',
        encoding="utf-8",
    )

    settings = Settings(config_path=config_path)

    assert settings.config_path == config_path
    assert settings.chroma_path == Path("/tmp/from-config")
    assert settings.wake_phrase == "hello lulu"
