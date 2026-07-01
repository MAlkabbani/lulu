from __future__ import annotations

from collections import deque

from audio_handler import AudioHandler, WakeMatch, text_similarity
from config import Settings
from main import (
    _cooldown_active,
    _remaining_window,
    _should_suppress_self_audio_echo,
    _window_active,
    parse_args,
)
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
