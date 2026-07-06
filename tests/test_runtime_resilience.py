from __future__ import annotations

from types import SimpleNamespace

from config import Settings
from llm_router import PreparedTurn, ToolTrace
import main as app_main
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


class BootstrapTestOllamaClient:
    def __init__(self, settings: Settings) -> None:  # noqa: ARG002
        pass


class BootstrapTestMemoryManager:
    def __init__(self, settings: Settings, ollama_client: BootstrapTestOllamaClient) -> None:  # noqa: ARG002
        pass


class BootstrapTestRouter:
    def __init__(
        self,
        settings: Settings,
        ollama_client: BootstrapTestOllamaClient,
        memory_manager: BootstrapTestMemoryManager,
    ) -> None:  # noqa: ARG002
        pass


class BootstrapTestAudioHandler:
    def __init__(self, settings: Settings) -> None:  # noqa: ARG002
        pass


class BootstrapTestTTS:
    instances: list["BootstrapTestTTS"] = []

    def __init__(self) -> None:
        self.closed = False
        BootstrapTestTTS.instances.append(self)

    def set_on_chunk_spoken(self, callback) -> None:  # noqa: ANN001, ARG002
        return None

    def set_on_chunk_error(self, callback) -> None:  # noqa: ANN001, ARG002
        return None

    def close(self) -> None:
        self.closed = True


class BootstrapTestUI:
    instances: list["BootstrapTestUI"] = []

    def __init__(self, settings: Settings) -> None:  # noqa: ARG002
        self.started = False
        self.stopped = False
        BootstrapTestUI.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


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


def test_main_cleans_up_ui_and_tts_when_bootstrap_fails(monkeypatch) -> None:
    BootstrapTestTTS.instances.clear()
    BootstrapTestUI.instances.clear()

    monkeypatch.setattr(
        app_main,
        "parse_args",
        lambda: SimpleNamespace(turn_based=False),
    )
    monkeypatch.setattr(app_main, "OllamaClient", BootstrapTestOllamaClient)
    monkeypatch.setattr(app_main, "MemoryManager", BootstrapTestMemoryManager)
    monkeypatch.setattr(app_main, "HybridRouter", BootstrapTestRouter)
    monkeypatch.setattr(app_main, "AudioHandler", BootstrapTestAudioHandler)
    monkeypatch.setattr(app_main, "MacOSTTS", BootstrapTestTTS)
    monkeypatch.setattr(app_main, "TerminalUI", BootstrapTestUI)
    monkeypatch.setattr(app_main, "_bootstrap_connection", lambda *args, **kwargs: False)

    app_main.main()

    ui = BootstrapTestUI.instances[-1]
    tts = BootstrapTestTTS.instances[-1]
    assert ui.started is True
    assert ui.stopped is True
    assert tts.closed is True


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
    assert ui.state.action_summary == "Save memory"
    assert ui.state.current_tool_status == "Save memory completed"
    assert "Saved memory via save_to_memory" in ui.state.last_tool_result
    assert ui.state.recent_saves[0] == "My dentist appointment is on Friday at 2 PM."
    assert any(event.startswith("Action selected: Save memory") for event in ui.state.recent_events)
    assert any(event.startswith("Action running: Save memory") for event in ui.state.recent_events)
    assert any(event.startswith("Action completed:") for event in ui.state.recent_events)


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
    assert ui.state.action_summary == "Answer only"
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

    assert ui.state.action_summary == "Check memory"
    assert ui.state.current_tool_status == "Action limit reached"
    assert ui.state.last_tool_result == "Stopped backend tool execution after 2 round(s)."
    assert any(event.startswith("Action limit reached:") for event in ui.state.recent_events)


def test_process_transcript_turn_surfaces_direct_save_status_in_ui() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            fixed_reply="Information explicitly saved to vault.",
            memory_hits=[],
            saved_items=["My dog's name is Nori."],
            invocation_path="explicit_save",
            invocation_summary="Deterministic memory save via insert info.",
        )
    )

    _process_transcript_turn(
        transcript="insert info my dog's name is Nori",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=StreamingOllamaClient([]),
        tts=FakeTTS(),
        ui=ui,
    )

    assert ui.state.invocation_path == "explicit_save"
    assert ui.state.action_summary == "Save memory directly"
    assert ui.state.current_tool_status == "Memory saved directly"
    assert ui.state.last_tool_result == "Saved via insert info command."


def test_process_transcript_turn_surfaces_multi_step_action_summary_in_ui() -> None:
    ui = TerminalUI(Settings())
    router = FixedReplyRouter(
        PreparedTurn(
            fixed_reply="I found the memory and explained why it matters.",
            memory_hits=[],
            saved_items=[],
            invocation_path="model_tool_call",
            invocation_summary=(
                "Natural-language backend actions succeeded: checked memory and explained a stored item."
            ),
            tool_traces=[
                ToolTrace(
                    tool_name="search_memory",
                    stage="selected",
                    detail="Round 1: selected backend action search_memory.",
                ),
                ToolTrace(
                    tool_name="search_memory",
                    stage="succeeded",
                    detail="Searched memory via search_memory and found 1 hit(s) for: tea",
                ),
                ToolTrace(
                    tool_name="explain_memory_hit",
                    stage="selected",
                    detail="Round 2: selected backend action explain_memory_hit.",
                ),
                ToolTrace(
                    tool_name="explain_memory_hit",
                    stage="succeeded",
                    detail="Explained memory tea-1 with category preference and revision count 1.",
                ),
            ],
        )
    )

    _process_transcript_turn(
        transcript="What did you find, and why does it matter?",
        settings=Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24),
        router=router,
        ollama_client=StreamingOllamaClient([]),
        tts=FakeTTS(),
        ui=ui,
    )

    assert ui.state.action_summary == "Check memory -> Explain memory"
    assert ui.state.current_tool_status == "Explain memory completed"
