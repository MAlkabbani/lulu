from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

import numpy as np

from app_core.runtime_controller import run_continuous_voice_loop
from audio_handler import (
    AudioCaptureError,
    AudioHandler,
    AudioTranscriptionError,
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
from wake_detection import WakeAudioAnalysis


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

    def fake_transcribe(  # noqa: ANN001
        audio,
        path_or_hf_repo: str,
        language: str,
        initial_prompt: str | None = None,
    ) -> dict[str, str]:
        captured["audio"] = audio
        captured["model"] = path_or_hf_repo
        captured["language"] = language
        captured["initial_prompt"] = initial_prompt
        return {"text": "hey lulu"}

    monkeypatch.setattr("audio_handler.transcribe", fake_transcribe)
    settings = Settings(**(build_settings().__dict__ | {"whisper_model": str(fake_model_dir)}))
    handler = AudioHandler(settings)

    transcript = handler.transcribe_audio(np.zeros(160, dtype=np.float32))

    assert transcript == "hey lulu"
    assert captured["model"] == str(fake_model_dir)
    assert captured["language"] == settings.whisper_language
    assert captured["initial_prompt"] is None
    assert isinstance(captured["audio"], np.ndarray)
    assert captured["audio"].shape == (160,)


def test_transcribe_audio_sanitizes_audio_before_whisper(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    fake_model_dir = tmp_path / "fake-whisper-model"
    fake_model_dir.mkdir()

    def fake_transcribe(  # noqa: ANN001
        audio,
        path_or_hf_repo: str,
        language: str,
        initial_prompt: str | None = None,
    ) -> dict[str, str]:
        captured["audio"] = audio
        captured["model"] = path_or_hf_repo
        captured["language"] = language
        captured["initial_prompt"] = initial_prompt
        return {"text": "sanitized"}

    monkeypatch.setattr("audio_handler.transcribe", fake_transcribe)
    handler = AudioHandler(Settings(whisper_model=str(fake_model_dir)))

    transcript = handler.transcribe_audio(
        np.array([np.nan, np.inf, -np.inf, 1.5, -1.5], dtype=np.float32)
    )

    assert transcript == "sanitized"
    assert np.array_equal(
        captured["audio"],
        np.array([0.0, 1.0, -1.0, 1.0, -1.0], dtype=np.float32),
    )
    assert captured["initial_prompt"] is None


def test_transcribe_audio_passes_initial_prompt_to_whisper(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    fake_model_dir = tmp_path / "fake-whisper-model"
    fake_model_dir.mkdir()

    def fake_transcribe(  # noqa: ANN001
        audio,
        path_or_hf_repo: str,
        language: str,
        initial_prompt: str | None = None,
    ) -> dict[str, str]:
        captured["audio"] = audio
        captured["model"] = path_or_hf_repo
        captured["language"] = language
        captured["initial_prompt"] = initial_prompt
        return {"text": "hey lulu"}

    monkeypatch.setattr("audio_handler.transcribe", fake_transcribe)
    handler = AudioHandler(Settings(whisper_model=str(fake_model_dir)))

    transcript = handler.transcribe_audio(
        np.zeros(160, dtype=np.float32),
        initial_prompt="hey lulu",
    )

    assert transcript == "hey lulu"
    assert captured["initial_prompt"] == "hey lulu"


def test_ensure_transcription_ready_runs_single_warmup_across_threads(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_model_dir = tmp_path / "fake-whisper-model"
    fake_model_dir.mkdir()
    handler = AudioHandler(Settings(whisper_model=str(fake_model_dir)))
    started = threading.Event()
    release = threading.Event()
    call_count = 0
    call_count_lock = threading.Lock()
    errors: list[Exception] = []

    def fake_transcribe(  # noqa: ANN001
        audio,
        path_or_hf_repo: str,
        language: str,
        initial_prompt: str | None = None,
    ):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
        started.set()
        release.wait(timeout=1.0)
        return {"text": ""}

    monkeypatch.setattr("audio_handler.transcribe", fake_transcribe)

    def worker() -> None:
        try:
            handler.ensure_transcription_ready()
        except Exception as exc:  # pragma: no cover - test should not hit this path
            errors.append(exc)

    first = threading.Thread(target=worker)
    second = threading.Thread(target=worker)
    first.start()
    assert started.wait(timeout=1.0) is True
    second.start()
    release.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert errors == []
    assert call_count == 1


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


def test_match_wake_phrase_rejects_i_love_prefix_phrase() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("I love what time it is")

    assert result.matched is False
    assert result.reason == "below-threshold"


def test_match_wake_phrase_rejects_hello_phrase_without_lulu_tokens() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("hey hello what's up")

    assert result.matched is False
    assert result.reason == "below-threshold"


def test_match_wake_phrase_rejects_he_looks_phrase() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("he looks up")

    assert result.matched is False
    assert result.reason == "below-threshold"


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


def test_practical_voice_mode_keeps_wake_scan_in_stt_path_when_acoustic_gate_is_low(
    monkeypatch,
) -> None:
    settings = Settings(practical_voice_mode=True)
    ui = TerminalUI(settings)
    audio = np.ones(160, dtype=np.float32)
    processed_audio = np.full(160, 0.5, dtype=np.float32)
    transcribed_audio: list[np.ndarray] = []

    class PracticalAudioHandler:
        def __init__(self) -> None:
            self.settings = settings

        def ensure_transcription_ready(self) -> None:
            return None

        def record_wake_scan(self):  # noqa: ANN201
            return audio

        def analyze_wake_audio(self, wake_audio: np.ndarray) -> WakeAudioAnalysis:
            assert np.array_equal(wake_audio, audio)
            return WakeAudioAnalysis(
                processed_audio=processed_audio,
                acoustic_score=0.30,
                dtw_score=0.28,
                confidence=0.31,
                dynamic_threshold=0.53,
                duration_seconds=0.45,
                snr_db=12.0,
                voiced_ratio=0.6,
                syllable_peaks=2,
                spectral_centroid_mean=210.0,
                zero_crossing_rate_mean=0.08,
                feature_frames=18,
                candidate=False,
                fast_path_eligible=False,
                reason="acoustic-reject",
                latency_ms=8.0,
            )

        def transcribe_audio(
            self,
            wake_audio: np.ndarray,
            *,
            initial_prompt: str | None = None,
        ) -> str:
            transcribed_audio.append(wake_audio.copy())
            assert initial_prompt == settings.wake_phrase
            stop_event.set()
            return ""

    monkeypatch.setattr(
        "app_core.runtime_controller.capture_audio",
        lambda capture_fn, ui, event_bus=None: (capture_fn(), False),
    )

    stop_event = threading.Event()

    run_continuous_voice_loop(
        settings,
        PracticalAudioHandler(),  # type: ignore[arg-type]
        router=None,  # type: ignore[arg-type]
        ollama_client=None,  # type: ignore[arg-type]
        tts=None,  # type: ignore[arg-type]
        ui=ui,
        recent_spoken_chunks=deque(),
        stop_event=stop_event,
    )

    assert len(transcribed_audio) == 1
    assert np.array_equal(transcribed_audio[0], processed_audio)
    assert any(
        "practical voice mode kept the STT wake scan enabled" in event
        for event in ui.state.recent_events
    )


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

    ready = _bootstrap_connection(FailingOllamaClient(), ui)

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
        settings = build_settings()

        def transcribe_audio(self, audio: np.ndarray, *, initial_prompt: str | None = None) -> str:
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


def test_transcribe_audio_wake_scan_updates_transcript_and_guidance() -> None:
    class SuccessfulAudioHandler:
        settings = build_settings()
        initial_prompt: str | None = None

        def transcribe_audio(  # noqa: ARG002
            self,
            audio: np.ndarray,
            *,
            initial_prompt: str | None = None,
        ) -> str:
            self.initial_prompt = initial_prompt
            return "hey lulu what time is it"

    handler = SuccessfulAudioHandler()
    ui = TerminalUI(build_settings())

    transcript = _transcribe_audio(
        handler,
        ui,
        np.zeros(160, dtype=np.float32),
        wake_scan=True,
    )

    assert transcript == "hey lulu what time is it"
    assert handler.initial_prompt == handler.settings.wake_phrase
    assert ui.state.transcript == "hey lulu what time is it"
    assert ui.state.response == ""
    assert ui.state.wake_guidance == "Checking whether the wake phrase matched what Whisper heard."
