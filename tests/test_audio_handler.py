from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from audio_handler import (
    AudioCaptureError,
    AudioHandler,
    AudioTranscriptionError,
    _split_remote_model_reference,
)
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


def test_record_until_silence_reports_missing_audio_dependency(monkeypatch) -> None:
    monkeypatch.setattr("audio_handler.sd", None)
    monkeypatch.setattr(
        "audio_handler._SOUNDDEVICE_IMPORT_ERROR",
        OSError("PortAudio library not found"),
    )
    handler = AudioHandler(Settings())

    with pytest.raises(AudioCaptureError, match="Audio input dependency is unavailable"):
        handler._record_until_silence(
            max_record_seconds=1.0,
            min_speech_seconds=0.2,
            silence_seconds=0.2,
            pre_roll_chunks=1,
        )


def test_transcribe_audio_reports_missing_mlx_runtime(monkeypatch) -> None:
    monkeypatch.setattr("audio_handler.transcribe", None)
    monkeypatch.setattr(
        "audio_handler._MLX_WHISPER_IMPORT_ERROR",
        ImportError("libmlx.so missing"),
    )
    handler = AudioHandler(Settings())
    monkeypatch.setattr(handler, "_resolve_whisper_model_reference", lambda: "mock-model")

    with pytest.raises(
        AudioTranscriptionError,
        match="Local Whisper transcription is unavailable",
    ):
        handler.transcribe_audio(np.zeros(8, dtype=np.float32))
