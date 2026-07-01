from __future__ import annotations

from audio_handler import AudioHandler, WakeMatch
from config import Settings
from main import _cooldown_active, _remaining_window, _window_active, parse_args


def build_settings() -> Settings:
    return Settings(
        wake_phrase="hey lulu",
        conversation_window_seconds=12.0,
        wake_cooldown_seconds=1.2,
    )


def test_match_wake_phrase_accepts_bare_phrase() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("hey lulu")

    assert result == WakeMatch(matched=True, remainder="")


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


def test_match_wake_phrase_rejects_non_wake_transcript() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("what time is it")

    assert result == WakeMatch(matched=False, remainder="")


def test_match_wake_phrase_rejects_close_but_unlisted_phrase() -> None:
    handler = AudioHandler(build_settings())

    result = handler.match_wake_phrase("hey luma what's up")

    assert result == WakeMatch(matched=False, remainder="")


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
