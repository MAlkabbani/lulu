from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from rich.columns import Columns
from rich.console import Console
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import Settings


@dataclass
class UIState:
    mode: str = "starting"
    status_line: str = "Booting Lulu..."
    transcript: str = ""
    response: str = ""
    memory_hit_count: int = 0
    recent_saves: deque[str] = field(default_factory=lambda: deque(maxlen=5))
    recent_events: deque[str] = field(default_factory=lambda: deque(maxlen=10))
    latencies_ms: dict[str, float] = field(default_factory=dict)
    ollama_version: str = "unknown"
    text_input_mode: bool = False


class TerminalUI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.console = Console()
        self.state = UIState()
        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=4,
            transient=False,
        )

    def start(self) -> None:
        self.live.start()
        self.refresh()

    def stop(self) -> None:
        self.live.stop()

    def refresh(self) -> None:
        self.live.update(self._render())

    def set_connection(self, version: str, text_input_mode: bool) -> None:
        self.state.ollama_version = version
        self.state.text_input_mode = text_input_mode
        self.state.status_line = f"{self.settings.app_name} is ready. Press Ctrl+C to stop."
        self.state.mode = "ready"
        self.log_event(f"Connected to Ollama {version}")
        self.refresh()

    def set_mode(self, mode: str, status_line: str | None = None) -> None:
        self.state.mode = mode
        if status_line is not None:
            self.state.status_line = status_line
        self.refresh()

    def set_transcript(self, transcript: str) -> None:
        self.state.transcript = transcript
        self.refresh()

    def set_response(self, response: str) -> None:
        self.state.response = response
        self.refresh()

    def set_memory_hits(self, count: int) -> None:
        self.state.memory_hit_count = count
        self.refresh()

    def record_latency(self, label: str, seconds: float) -> None:
        self.state.latencies_ms[label] = seconds * 1000
        self.refresh()

    def add_saved_items(self, saved_items: list[str]) -> None:
        for item in saved_items:
            self.state.recent_saves.appendleft(item)
            self.log_event(f"Saved memory: {item}", refresh=False)
        self.refresh()

    def log_event(self, event: str, refresh: bool = True) -> None:
        self.state.recent_events.appendleft(event)
        if refresh:
            self.refresh()

    def reset_turn(self) -> None:
        self.state.memory_hit_count = 0
        self.state.latencies_ms = {}
        self.state.transcript = ""
        self.state.response = ""
        self.refresh()

    def prompt_text(self) -> str:
        self.live.stop()
        try:
            return self.console.input("\nYou> ").strip()
        finally:
            self.live.start()
            self.refresh()

    def _render(self) -> Group:
        top_row = Columns(
            [
                Panel(self._render_status(), title="Status", expand=True),
                Panel(self._render_latencies(), title="Latencies", expand=True),
            ],
            expand=True,
        )
        middle_row = Columns(
            [
                Panel(self._render_transcript(), title="Transcript", expand=True),
                Panel(self._render_response(), title="Response", expand=True),
            ],
            expand=True,
        )
        bottom_row = Columns(
            [
                Panel(self._render_saves(), title="Recent Memory Saves", expand=True),
                Panel(self._render_events(), title="Recent Turn Events", expand=True),
            ],
            expand=True,
        )
        return Group(top_row, middle_row, bottom_row)

    def _render_status(self) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_row("Assistant", self.settings.app_name)
        table.add_row("Mode", self.state.mode)
        table.add_row("Input", "text" if self.state.text_input_mode else "voice")
        table.add_row("Ollama", self.state.ollama_version)
        table.add_row("Recall hits", str(self.state.memory_hit_count))
        table.add_row("State", self.state.status_line)
        return table

    def _render_latencies(self) -> Table:
        table = Table.grid(padding=(0, 1))
        if not self.state.latencies_ms:
            table.add_row("No timing data yet.")
            return table

        for label in ("capture", "stt", "router", "tts", "total"):
            if label in self.state.latencies_ms:
                table.add_row(label, f"{self.state.latencies_ms[label]:.1f} ms")
        return table

    def _render_transcript(self) -> Text:
        return Text(self.state.transcript or "No transcript yet.", overflow="fold")

    def _render_response(self) -> Text:
        return Text(self.state.response or "No response yet.", overflow="fold")

    def _render_saves(self) -> Table:
        table = Table.grid(padding=(0, 1))
        if not self.state.recent_saves:
            table.add_row("No saved memories yet.")
            return table

        for item in self.state.recent_saves:
            table.add_row(item)
        return table

    def _render_events(self) -> Table:
        table = Table.grid(padding=(0, 1))
        if not self.state.recent_events:
            table.add_row("No events yet.")
            return table

        for event in self.state.recent_events:
            table.add_row(event)
        return table
