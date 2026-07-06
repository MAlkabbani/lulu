from __future__ import annotations

from pathlib import Path

import pytest

from audio_handler import AudioHandler, AudioTranscriptionError, _split_remote_model_reference
from config import Settings


def test_split_remote_model_reference_prefers_embedded_revision() -> None:
    repo_id, revision = _split_remote_model_reference("mlx-community/whisper-base-mlx@abc123", "")

    assert repo_id == "mlx-community/whisper-base-mlx"
    assert revision == "abc123"


def test_resolve_whisper_model_reference_uses_pinned_revision(monkeypatch, tmp_path: Path) -> None:
    recorded_calls: list[dict[str, object]] = []
    resolved_path = tmp_path / "cached-model"
    resolved_path.mkdir()

    def fake_snapshot_download(**kwargs: object) -> str:
        recorded_calls.append(kwargs)
        return str(resolved_path)

    monkeypatch.setattr("audio_handler.snapshot_download", fake_snapshot_download)
    handler = AudioHandler(
        Settings(
            whisper_model="mlx-community/whisper-base-mlx",
            whisper_model_revision="abc123",
        )
    )

    result = handler._resolve_whisper_model_reference()

    assert result == str(resolved_path)
    assert recorded_calls == [
        {
            "repo_id": "mlx-community/whisper-base-mlx",
            "local_files_only": True,
            "revision": "abc123",
        }
    ]


def test_resolve_whisper_model_reference_rejects_unpinned_remote_download(monkeypatch) -> None:
    recorded_calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs: object) -> str:
        recorded_calls.append(kwargs)
        raise RuntimeError("missing cache")

    monkeypatch.setattr("audio_handler.snapshot_download", fake_snapshot_download)
    handler = AudioHandler(Settings(whisper_model="mlx-community/whisper-base-mlx"))

    with pytest.raises(AudioTranscriptionError, match="pinned revision"):
        handler._resolve_whisper_model_reference()

    assert recorded_calls == [
        {
            "repo_id": "mlx-community/whisper-base-mlx",
            "local_files_only": True,
            "revision": None,
        }
    ]
