from __future__ import annotations

from config import Settings
from llm_router import PreparedTurn
from main import _process_transcript_turn
from ollama_client import OllamaClientError
from audio_handler import TTSPlaybackError
from terminal_ui import TerminalUI


class FailingRouter:
    def prepare_turn(self, transcript: str) -> PreparedTurn:  # noqa: ARG002
        raise RuntimeError("memory backend unavailable")


class FixedReplyRouter:
    def __init__(self, prepared: PreparedTurn) -> None:
        self.prepared = prepared

    def prepare_turn(self, transcript: str) -> PreparedTurn:  # noqa: ARG002
        return self.prepared


class StreamingOllamaClient:
    def __init__(self, pieces: list[str], error: Exception | None = None) -> None:
        self.pieces = pieces
        self.error = error

    def stream_chat(self, messages):  # noqa: ANN001, D401
        for piece in self.pieces:
            yield piece
        if self.error is not None:
            raise self.error


class FakeTTS:
    def __init__(self, turn_errors: list[TTSPlaybackError] | None = None) -> None:
        self.turn_errors = turn_errors or []
        self.enqueued_chunks: list[str] = []
        self.started = 0
        self.finished = 0

    def start_turn(self) -> None:
        self.started += 1

    def enqueue_chunk(self, chunk: str) -> None:
        self.enqueued_chunks.append(chunk)

    def finish_turn(self) -> list[TTSPlaybackError]:
        self.finished += 1
        return list(self.turn_errors)


def test_process_transcript_turn_reports_router_failure() -> None:
    ui = TerminalUI(Settings())

    _process_transcript_turn(
        transcript="remember this",
        settings=Settings(),
        router=FailingRouter(),
        ollama_client=StreamingOllamaClient([]),
        tts=FakeTTS(),
        ui=ui,
    )

    assert ui.state.mode == "router_error"
    assert "memory backend unavailable" in ui.state.status_line


def test_process_transcript_turn_preserves_partial_text_on_stream_failure() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            final_messages=[{"role": "user", "content": "hi"}],
            memory_hits=[],
            saved_items=[],
        )
    )
    ollama = StreamingOllamaClient(
        ["Partial response"],
        error=OllamaClientError("connection dropped mid-stream"),
    )

    _process_transcript_turn(
        transcript="hi",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=ollama,
        tts=FakeTTS(),
        ui=ui,
    )

    assert ui.state.mode == "stream_error"
    assert ui.state.response == "Partial response"
    assert "connection dropped mid-stream" in ui.state.status_line


def test_process_transcript_turn_flushes_buffered_speech_on_stream_failure() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            final_messages=[{"role": "user", "content": "hi"}],
            memory_hits=[],
            saved_items=[],
        )
    )
    ollama = StreamingOllamaClient(
        ["Partial response"],
        error=OllamaClientError("connection dropped mid-stream"),
    )
    tts = FakeTTS()

    _process_transcript_turn(
        transcript="hi",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=ollama,
        tts=tts,
        ui=ui,
    )

    assert tts.enqueued_chunks == ["Partial response"]
    assert tts.finished == 1


def test_process_transcript_turn_keeps_final_text_when_tts_fails() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            fixed_reply="Hello there.",
            memory_hits=[],
            saved_items=[],
        )
    )
    tts = FakeTTS([TTSPlaybackError("Hello there.", "macOS say failed")])

    _process_transcript_turn(
        transcript="hello",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=StreamingOllamaClient([]),
        tts=tts,
        ui=ui,
    )

    assert ui.state.mode == "tts_error"
    assert ui.state.response == "Hello there."
    assert "macOS say failed" in ui.state.status_line
