from __future__ import annotations

from types import SimpleNamespace

from audio_handler import MacOSTTS, PhraseChunker, TTSPlaybackError
from config import Settings
from main import _stream_and_chunk


class FakeUI:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.emitted_chunks: list[str] = []

    def set_response(self, response: str) -> None:
        self.responses.append(response)

    def record_emitted_chunk(self, chunk: str) -> None:
        self.emitted_chunks.append(chunk)


class FakeTTS:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def enqueue_chunk(self, chunk: str) -> None:
        self.chunks.append(chunk)


def build_settings() -> Settings:
    return Settings(
        tts_stream_min_chunk_chars=8,
        tts_stream_soft_chunk_chars=24,
        tts_stream_max_chunk_chars=40,
    )


def test_phrase_chunker_emits_phrase_boundary_chunks() -> None:
    chunker = PhraseChunker(build_settings())

    ready = chunker.push("Thanks, I found your note, and")

    assert ready == ["Thanks,", "I found your note,"]
    assert chunker.finish() == ["and"]


def test_phrase_chunker_flushes_tail_without_punctuation() -> None:
    chunker = PhraseChunker(build_settings())

    ready = chunker.push("This stays buffered until the end")

    assert ready == ["This stays buffered"]
    assert chunker.finish() == ["until the end"]


def test_stream_and_chunk_accumulates_text_and_emits_in_order() -> None:
    chunker = PhraseChunker(build_settings())
    tts = FakeTTS()
    ui = FakeUI()
    response_parts: list[str] = []

    streamed = list(
        _stream_and_chunk(
            stream_source=iter(["Hello there, ", "friend."]),
            chunker=chunker,
            tts=tts,
            ui=ui,
            response_parts=response_parts,
        )
    )

    assert streamed == ["Hello there, ", "friend."]
    assert "".join(response_parts) == "Hello there, friend."
    assert tts.chunks == ["Hello there,", "friend."]
    assert ui.emitted_chunks == ["Hello there,", "friend."]


def test_macos_tts_queue_preserves_chunk_order(monkeypatch) -> None:
    spoken: list[str] = []

    def fake_run(
        command: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> SimpleNamespace:
        spoken.append(command[1])
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("audio_handler.subprocess.run", fake_run)
    tts = MacOSTTS()

    try:
        tts.start_turn()
        tts.enqueue_chunk("first chunk")
        tts.enqueue_chunk("second chunk")
        tts.finish_turn()
    finally:
        tts.close()

    assert spoken == ["first chunk", "second chunk"]


def test_macos_tts_reports_failures_without_marking_chunk_spoken(monkeypatch) -> None:
    spoken: list[str] = []
    reported_errors: list[TTSPlaybackError] = []

    def fake_run(
        command: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stderr=f"failed to speak {command[1]}")

    monkeypatch.setattr("audio_handler.subprocess.run", fake_run)
    tts = MacOSTTS()
    tts.set_on_chunk_spoken(spoken.append)
    tts.set_on_chunk_error(reported_errors.append)

    try:
        tts.start_turn()
        tts.enqueue_chunk("broken chunk")
        turn_errors = tts.finish_turn()
    finally:
        tts.close()

    assert spoken == []
    assert len(reported_errors) == 1
    assert len(turn_errors) == 1
    assert reported_errors[0].chunk == "broken chunk"
    assert "failed to speak broken chunk" in str(turn_errors[0])
