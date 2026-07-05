from __future__ import annotations

from collections import deque
from pathlib import Path
import numpy as np

from audio_handler import (
    AudioCaptureError,
    AudioHandler,
    AudioTranscriptionError,
    WakeMatch,
    _resolve_input_device,
    text_similarity,
)
from config import Settings
from main import (
    _bootstrap_connection,
    _capture_audio,
    _cooldown_active,
    _remaining_window,
    _should_suppress_self_audio_echo,
    _transcribe_audio,
    _wake_rejection_guidance,
    _wake_rejection_response,
    _window_active,
    parse_args,
)
from ollama_client import OllamaClientError
from terminal_ui import TerminalUI


def build_settings() -> Settings:
    return Settings(
        wake_phrase="hey lulu",
        conversation_window_seconds=12.0,
        wake_cooldown_seconds=1.2,
        self_audio_guard_seconds=8.0,
        self_audio_similarity_threshold=0.74,
        wake_match_score_threshold=0.86,
    )


def test_match_wake_phrase_accepts_bare_phrase() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("hey lulu")

    assert result.matched is True
    assert result.remainder == ""
    assert result.score >= 0.99


def test_transcribe_audio_uses_configured_whisper_language(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    fake_model_dir = tmp_path / "fake-whisper-model"
    fake_model_dir.mkdir()

    def fake_transcribe(path: str, path_or_hf_repo: str, language: str) -> dict[str, str]:
        captured["path"] = path
        captured["model"] = path_or_hf_repo
        captured["language"] = language
        return {"text": "hey lulu"}

    monkeypatch.setattr("audio_handler.transcribe", fake_transcribe)
    settings = Settings(**(build_settings().__dict__ | {"whisper_model": str(fake_model_dir)}))
    handler = AudioHandler(settings)

    transcript = handler.transcribe_audio(np.zeros(160, dtype=np.float32))

    assert transcript == "hey lulu"
    assert captured["model"] == str(fake_model_dir)
    assert captured["language"] == settings.whisper_language
    assert Path(str(captured["path"])).suffix == ".wav"


def test_match_wake_phrase_extracts_inline_request() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("Hey Lulu what time is it")

    assert result.matched is True
    assert result.remainder == "what time is it"


def test_match_wake_phrase_accepts_common_whisper_variant() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("hey lu lu play some jazz")

    assert result.matched is True
    assert result.remainder == "play some jazz"
    assert result.score >= 0.86


def test_match_wake_phrase_accepts_scored_whisper_confusion() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("hay lou lou set a timer")

    assert result.matched is True
    assert result.remainder == "set a timer"
    assert result.score >= 0.86


def test_match_wake_phrase_accepts_i_love_prefix_confusion() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("I love what time it is")

    assert result.matched is True
    assert result.remainder == "what time it is"
    assert result.score >= 0.86


def test_match_wake_phrase_accepts_leading_filler_before_wake_phrase() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("um hey lulu what time is it")

    assert result.matched is True
    assert result.remainder == "what time is it"
    assert result.score >= 0.86


def test_resolve_input_device_accepts_blank_numeric_and_named_values() -> None:
    assert _resolve_input_device("") is None
    assert _resolve_input_device("1") == 1
    assert _resolve_input_device("MacBook Air Microphone") == "MacBook Air Microphone"


def test_match_wake_phrase_rejects_non_wake_transcript() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("what time is it")

    assert result.matched is False
    assert result.reason in {"below-threshold", "too-short"}


def test_match_wake_phrase_rejects_close_but_unlisted_phrase() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("hey luma what's up")

    assert result.matched is False
    assert result.score < 0.86


def test_conversation_window_helpers_track_expiry() -> None:
    now = 100.0
    deadline = 112.0

    assert _window_active(now, deadline) is True
    assert _remaining_window(now, deadline) == 12.0
    assert _window_active(112.0, deadline) is False
    assert _remaining_window(120.0, deadline) == 0.0


def test_cooldown_helper_suppresses_wake_checks_until_expiry() -> None:
    cooldown_until = 10.5

    assert _cooldown_active(10.0, cooldown_until) is True
    assert _cooldown_active(10.5, cooldown_until) is False
    assert _cooldown_active(11.0, cooldown_until) is False


def test_parse_args_accepts_turn_based_flag(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["main.py", "--turn-based"])

    args = parse_args()

    assert args.turn_based is True


def test_terminal_ui_runtime_badge_reflects_turn_based_mode() -> None:
    ui = TerminalUI(build_settings())

    ui.set_runtime_mode("turn-based")

    assert ui._runtime_badge().plain == "TURN-BASED"


def test_terminal_ui_records_recent_wake_attempts() -> None:
    ui = TerminalUI(build_settings())

    ui.record_wake_attempt(
        transcript="hay lou lou set a timer",
        score=0.91,
        accepted=True,
        reason="score-match",
    )

    assert ui.state.last_wake_score == 0.91
    assert ui.state.last_wake_decision == "accepted (score-match)"
    assert ui.state.recent_wake_attempts[0].startswith("ACCEPTED score=0.91")
    assert ui.state.accepted_wake_attempts == 1
    assert ui.state.wake_score_buckets["0.86-0.94"] == 1
    assert ui._wake_success_rate_text().plain == "100%"
    assert ui._wake_average_score_text().plain == "0.91"


def test_self_audio_guard_suppresses_recent_similar_reply() -> None:
    settings = build_settings()

    result = _should_suppress_self_audio_echo(
        transcript="I found your note and saved it to memory",
        last_assistant_reply="I found your note and saved it to memory.",
        last_assistant_reply_at=95.0,
        recent_spoken_chunks=deque(),
        now=100.0,
        settings=settings,
    )

    assert result is True


def test_self_audio_guard_ignores_old_or_dissimilar_reply() -> None:
    settings = build_settings()

    old_result = _should_suppress_self_audio_echo(
        transcript="I found your note and saved it to memory",
        last_assistant_reply="I found your note and saved it to memory.",
        last_assistant_reply_at=80.0,
        recent_spoken_chunks=deque(),
        now=100.0,
        settings=settings,
    )
    different_result = _should_suppress_self_audio_echo(
        transcript="hey lulu what's the weather",
        last_assistant_reply="I found your note and saved it to memory.",
        last_assistant_reply_at=95.0,
        recent_spoken_chunks=deque(),
        now=100.0,
        settings=settings,
    )

    assert old_result is False
    assert different_result is False


def test_text_similarity_normalizes_punctuation() -> None:
    assert text_similarity("Hello, Lulu!", "hello lulu") >= 0.95


def test_self_audio_guard_uses_recent_spoken_chunks() -> None:
    settings = build_settings()

    result = _should_suppress_self_audio_echo(
        transcript="saved it to memory",
        last_assistant_reply="Completely different final response",
        last_assistant_reply_at=95.0,
        recent_spoken_chunks=deque(
            [
                ("I found your note", 96.0),
                ("saved it to memory", 98.0),
            ]
        ),
        now=100.0,
        settings=settings,
    )

    assert result is True


def test_terminal_ui_records_rejected_wake_attempt_counters() -> None:
    ui = TerminalUI(build_settings())

    ui.record_wake_attempt(
        transcript="hello there",
        score=0.42,
        accepted=False,
        reason="below-threshold",
    )

    assert ui.state.rejected_wake_attempts == 1
    assert ui.state.wake_score_buckets["<0.50"] == 1
    assert ui.state.wake_rejection_reasons["below-threshold"] == 1
    assert "below-threshold:1" in ui._wake_rejection_reason_text().plain


def test_settings_practical_voice_mode_relaxes_wake_defaults() -> None:
    settings = Settings(practical_voice_mode=True)

    assert settings.wake_scan_max_record_seconds >= 3.5
    assert settings.wake_scan_silence_seconds >= 0.55
    assert settings.wake_scan_pre_roll_chunks >= 8
    assert settings.conversation_window_seconds >= 14.0
    assert settings.wake_match_score_threshold <= 0.84


def test_wake_rejection_helpers_offer_practical_guidance() -> None:
    settings = build_settings()

    too_short = _wake_rejection_response("too-short", 0.0, settings)
    below_threshold = _wake_rejection_guidance("below-threshold", settings)

    assert "short fragment" in too_short
    assert settings.wake_phrase in too_short
    assert settings.wake_phrase in below_threshold


def test_bootstrap_connection_reports_startup_failure_when_ollama_is_down() -> None:
    class FailingOllamaClient:
        def healthcheck(self):  # noqa: ANN202
            raise OllamaClientError("Unable to reach Ollama at http://localhost:11434.")

    ui = TerminalUI(build_settings())

    ready = _bootstrap_connection(FailingOllamaClient(), ui, text_input_mode=False)

    assert ready is False
    assert ui.state.mode == "startup_error"
    assert "Ollama startup check failed" in ui.state.status_line


def test_capture_audio_reports_dependency_error_on_microphone_failure() -> None:
    ui = TerminalUI(build_settings())

    def fail_capture() -> None:
        raise AudioCaptureError("microphone permission missing")

    audio, capture_failed = _capture_audio(fail_capture, ui)

    assert audio is None
    assert capture_failed is True
    assert ui.state.mode == "capture_error"
    assert "microphone permission missing" in ui.state.status_line


def test_record_wake_scan_uses_wake_specific_capture_settings(monkeypatch) -> None:
    captured: dict[str, float | int] = {}
    handler = AudioHandler(build_settings())

    def fake_record_until_silence(
        max_record_seconds: float,
        min_speech_seconds: float,
        silence_seconds: float,
        pre_roll_chunks: int,
    ) -> None:
        captured["max_record_seconds"] = max_record_seconds
        captured["min_speech_seconds"] = min_speech_seconds
        captured["silence_seconds"] = silence_seconds
        captured["pre_roll_chunks"] = pre_roll_chunks
        return None

    monkeypatch.setattr(handler, "_record_until_silence", fake_record_until_silence)

    handler.record_wake_scan()

    assert captured == {
        "max_record_seconds": handler.settings.wake_scan_max_record_seconds,
        "min_speech_seconds": handler.settings.wake_scan_min_speech_seconds,
        "silence_seconds": handler.settings.wake_scan_silence_seconds,
        "pre_roll_chunks": handler.settings.wake_scan_pre_roll_chunks,
    }


def test_transcribe_audio_reports_dependency_error_on_whisper_failure() -> None:
    class FailingAudioHandler:
        def transcribe_audio(self, audio: np.ndarray) -> str:
            raise AudioTranscriptionError("mlx whisper model load failed")

    ui = TerminalUI(build_settings())

    transcript = _transcribe_audio(
        FailingAudioHandler(),
        ui,
        np.zeros(160, dtype=np.float32),
    )

    assert transcript == ""
    assert ui.state.mode == "stt_error"
    assert "mlx whisper model load failed" in ui.state.status_line


def test_transcribe_audio_wake_scan_updates_transcript_and_response() -> None:
    class SuccessfulAudioHandler:
        def transcribe_audio(self, audio: np.ndarray) -> str:  # noqa: ARG002
            return "hey lulu what time is it"

    ui = TerminalUI(build_settings())

    transcript = _transcribe_audio(
        SuccessfulAudioHandler(),
        ui,
        np.zeros(160, dtype=np.float32),
        wake_scan=True,
    )

    assert transcript == "hey lulu what time is it"
    assert ui.state.transcript == "hey lulu what time is it"
    assert ui.state.response == "Wake scan captured speech. Matching wake phrase..."
