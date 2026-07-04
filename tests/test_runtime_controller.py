from __future__ import annotations

import threading
import time

from llm_router import PreparedTurn
from app_core.event_bus import EventBus
import app_core.runtime_controller as runtime_controller_module
from app_core.runtime_controller import RuntimeController
from config import Settings
from terminal_ui import TerminalUI


class FakeOllama:
    def __init__(self, version: str = "0.3.0", pieces: list[str] | None = None) -> None:
        self.version = version
        self.pieces = pieces or ["Hello from Lulu."]

    def healthcheck(self) -> dict[str, str]:
        return {"version": self.version}

    def stream_chat(self, messages):  # noqa: ANN001
        yield from self.pieces


class FakeMemoryManager:
    def __init__(self) -> None:
        self.saved: list[str] = []


class FixedRouter:
    def prepare_turn(self, transcript: str) -> PreparedTurn:
        return PreparedTurn(
            fixed_reply=f"Echo: {transcript}",
            memory_hits=[],
            saved_items=[],
            invocation_path="chat_only",
            invocation_summary="Normal chat reply; no backend action requested.",
        )


class FakeAudioHandler:
    def record_until_silence(self):  # noqa: ANN201
        return None

    def record_wake_scan(self):  # noqa: ANN201
        return None


class FakeTTS:
    def __init__(self) -> None:
        self.enqueued_chunks: list[str] = []
        self._on_chunk_spoken = None
        self._on_chunk_error = None
        self.closed = False

    def set_on_chunk_spoken(self, callback) -> None:  # noqa: ANN001
        self._on_chunk_spoken = callback

    def set_on_chunk_error(self, callback) -> None:  # noqa: ANN001
        self._on_chunk_error = callback

    def start_turn(self) -> None:
        return None

    def enqueue_chunk(self, chunk: str) -> None:
        self.enqueued_chunks.append(chunk)
        if self._on_chunk_spoken is not None:
            self._on_chunk_spoken(chunk)

    def finish_turn(self):  # noqa: ANN201
        return []

    def close(self) -> None:
        self.closed = True


def test_runtime_controller_bootstrap_sets_ready_state() -> None:
    settings = Settings()
    ui = TerminalUI(settings)
    controller = RuntimeController(
        settings,
        ollama_client=FakeOllama(),
        memory_manager=FakeMemoryManager(),  # type: ignore[arg-type]
        router=FixedRouter(),  # type: ignore[arg-type]
        audio_handler=FakeAudioHandler(),  # type: ignore[arg-type]
        tts=FakeTTS(),  # type: ignore[arg-type]
        ui=ui,
    )

    ready = controller.bootstrap(text_input_mode=True)

    assert ready is True
    assert ui.state.mode == "ready"
    assert ui.state.text_input_mode is True
    assert ui.state.ollama_version == "0.3.0"


def test_runtime_controller_set_runtime_mode_publishes_event() -> None:
    settings = Settings()
    ui = TerminalUI(settings)
    bus = EventBus()
    seen_events: list[str] = []
    bus.subscribe(lambda event: seen_events.append(event.event_type))
    controller = RuntimeController(
        settings,
        ollama_client=FakeOllama(),
        memory_manager=FakeMemoryManager(),  # type: ignore[arg-type]
        router=FixedRouter(),  # type: ignore[arg-type]
        audio_handler=FakeAudioHandler(),  # type: ignore[arg-type]
        tts=FakeTTS(),  # type: ignore[arg-type]
        ui=ui,
        event_bus=bus,
    )

    controller.set_runtime_mode("text")

    assert ui.state.runtime_mode == "text"
    assert "runtime.state_changed" in seen_events


def test_runtime_controller_submit_text_turn_updates_ui_and_tts() -> None:
    settings = Settings(tts_stream_min_chunk_chars=8, tts_stream_soft_chunk_chars=24)
    ui = TerminalUI(settings)
    tts = FakeTTS()
    controller = RuntimeController(
        settings,
        ollama_client=FakeOllama(),
        memory_manager=FakeMemoryManager(),  # type: ignore[arg-type]
        router=FixedRouter(),  # type: ignore[arg-type]
        audio_handler=FakeAudioHandler(),  # type: ignore[arg-type]
        tts=tts,  # type: ignore[arg-type]
        ui=ui,
    )

    controller.submit_text_turn("hello")

    assert ui.state.response == "Echo: hello"
    assert ui.state.mode == "ready"
    assert tts.enqueued_chunks == ["Echo: hello"]


def test_runtime_controller_starts_and_stops_continuous_background_runtime(monkeypatch) -> None:
    settings = Settings()
    ui = TerminalUI(settings)
    tts = FakeTTS()
    bus = EventBus()
    started = threading.Event()
    stopped = threading.Event()

    def fake_continuous_loop(  # noqa: PLR0913, ANN001
        settings,
        audio_handler,
        router,
        ollama_client,
        tts,
        ui,
        recent_spoken_chunks,
        stop_event=None,
        *,
        event_bus=None,
    ) -> None:
        started.set()
        while stop_event is not None and not stop_event.is_set():
            time.sleep(0.01)
        stopped.set()

    monkeypatch.setattr(runtime_controller_module, "run_continuous_voice_loop", fake_continuous_loop)
    controller = RuntimeController(
        settings,
        ollama_client=FakeOllama(),
        memory_manager=FakeMemoryManager(),  # type: ignore[arg-type]
        router=FixedRouter(),  # type: ignore[arg-type]
        audio_handler=FakeAudioHandler(),  # type: ignore[arg-type]
        tts=tts,  # type: ignore[arg-type]
        ui=ui,
        event_bus=bus,
    )

    state = controller.start_runtime("continuous")

    assert state.runtime_mode == "continuous"
    assert started.wait(0.5) is True

    stopped_state = controller.stop_runtime()

    assert stopped.wait(1.0) is True
    assert stopped_state.mode == "idle"
