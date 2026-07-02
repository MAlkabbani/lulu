from __future__ import annotations

from config import Settings
from llm_router import PreparedTurn, ToolTrace
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


def test_terminal_ui_records_first_spoken_latency_on_first_chunk() -> None:
    ui = TerminalUI(Settings())

    ui.reset_turn()
    ui.record_spoken_chunk("hello there")

    assert "first_spoken" in ui.state.latencies_ms
    assert ui.state.spoken_chunk_count == 1


def test_process_transcript_turn_surfaces_tool_success_in_ui() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            fixed_reply="Saved. I will remember that.",
            memory_hits=[],
            saved_items=["My dentist appointment is on Friday at 2 PM."],
            invocation_path="model_tool_call",
            invocation_summary=(
                "Natural-language backend action succeeded: "
                "Saved memory via save_to_memory: My dentist appointment is on Friday at 2 PM."
            ),
            tool_traces=[
                ToolTrace(
                    tool_name="save_to_memory",
                    stage="selected",
                    detail="Selected backend action save_to_memory.",
                ),
                ToolTrace(
                    tool_name="save_to_memory",
                    stage="running",
                    detail="Running backend action save_to_memory.",
                ),
                ToolTrace(
                    tool_name="save_to_memory",
                    stage="succeeded",
                    detail=(
                        "Saved memory via save_to_memory: "
                        "My dentist appointment is on Friday at 2 PM."
                    ),
                ),
            ],
        )
    )

    _process_transcript_turn(
        transcript="Please remember my dentist appointment is on Friday at 2 PM.",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=StreamingOllamaClient([]),
        tts=FakeTTS(),
        ui=ui,
    )

    assert ui.state.invocation_path == "model_tool_call"
    assert ui.state.current_tool_status == "save_to_memory succeeded"
    assert "Saved memory via save_to_memory" in ui.state.last_tool_result
    assert ui.state.recent_saves[0] == "My dentist appointment is on Friday at 2 PM."
    assert any(event.startswith("Tool selected: save_to_memory") for event in ui.state.recent_events)
    assert any(event.startswith("Tool running: save_to_memory") for event in ui.state.recent_events)
    assert any(event.startswith("Tool succeeded:") for event in ui.state.recent_events)


def test_process_transcript_turn_surfaces_chat_only_invocation_in_ui() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            fixed_reply="You like jasmine tea.",
            memory_hits=[],
            saved_items=[],
            invocation_path="chat_only",
            invocation_summary="Normal chat reply; no backend action requested.",
        )
    )

    _process_transcript_turn(
        transcript="What tea do I like?",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=StreamingOllamaClient([]),
        tts=FakeTTS(),
        ui=ui,
    )

    assert ui.state.invocation_path == "chat_only"
    assert ui.state.current_tool_status == "No backend tool used."
    assert any(
        event == "Normal chat reply; no backend action requested."
        for event in ui.state.recent_events
    )


def test_process_transcript_turn_surfaces_tool_limit_in_ui() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            fixed_reply="I stopped after the configured backend steps.",
            memory_hits=[],
            saved_items=[],
            invocation_path="model_tool_call",
            invocation_summary=(
                "Natural-language backend actions succeeded: 2 step(s) completed. "
                "Stopped backend tool execution after 2 round(s)."
            ),
            tool_traces=[
                ToolTrace(
                    tool_name="search_memory",
                    stage="selected",
                    detail="Round 1: selected backend action search_memory.",
                ),
                ToolTrace(
                    tool_name="search_memory",
                    stage="running",
                    detail="Round 1: running backend action search_memory.",
                ),
                ToolTrace(
                    tool_name="search_memory",
                    stage="succeeded",
                    detail="Searched memory via search_memory and found 1 hit(s) for: tea",
                ),
                ToolTrace(
                    tool_name="tool_loop_limit",
                    stage="limit_reached",
                    detail="Stopped backend tool execution after 2 round(s).",
                ),
            ],
        )
    )

    _process_transcript_turn(
        transcript="Keep checking my tea memories.",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=StreamingOllamaClient([]),
        tts=FakeTTS(),
        ui=ui,
    )

    assert ui.state.current_tool_status == "tool limit reached"
    assert ui.state.last_tool_result == "Stopped backend tool execution after 2 round(s)."
    assert any(event.startswith("Tool limit reached:") for event in ui.state.recent_events)
