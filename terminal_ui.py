from __future__ import annotations

from collections import deque
from collections import Counter
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
    emitted_chunk_count: int = 0
    spoken_chunk_count: int = 0
    recent_saves: deque[str] = field(default_factory=lambda: deque(maxlen=5))
    recent_events: deque[str] = field(default_factory=lambda: deque(maxlen=10))
    recent_wake_attempts: deque[str] = field(default_factory=lambda: deque(maxlen=6))
    latencies_ms: dict[str, float] = field(default_factory=dict)
    ollama_version: str = "unknown"
    text_input_mode: bool = False
    runtime_mode: str = "continuous"
    conversation_window_remaining: float | None = None
    cooldown_remaining: float | None = None
    last_wake_score: float | None = None
    last_wake_decision: str = "No wake attempts yet."
    wake_score_threshold: float | None = None
    accepted_wake_attempts: int = 0
    rejected_wake_attempts: int = 0
    wake_score_buckets: Counter[str] = field(default_factory=Counter)


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
        self.state.wake_score_threshold = self.settings.wake_match_score_threshold
        self.state.status_line = f"{self.settings.app_name} is ready. Press Ctrl+C to stop."
        self.state.mode = "ready"
        self.log_event(f"Connected to Ollama {version}")
        self.refresh()

    def set_runtime_mode(self, runtime_mode: str) -> None:
        self.state.runtime_mode = runtime_mode
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

    def record_emitted_chunk(self, chunk: str) -> None:
        self.state.emitted_chunk_count += 1
        self.log_event(
            f"Emitted speech chunk {self.state.emitted_chunk_count}: {self._truncate(chunk)}",
            refresh=False,
        )
        self.refresh()

    def record_spoken_chunk(self, chunk: str) -> None:
        self.state.spoken_chunk_count += 1
        self.log_event(
            f"Spoke chunk {self.state.spoken_chunk_count}: {self._truncate(chunk)}",
            refresh=False,
        )
        self.refresh()

    def set_conversation_window_remaining(self, seconds: float | None) -> None:
        self.state.conversation_window_remaining = seconds
        self.refresh()

    def set_cooldown_remaining(self, seconds: float | None) -> None:
        self.state.cooldown_remaining = seconds
        self.refresh()

    def record_wake_attempt(
        self,
        transcript: str,
        score: float,
        accepted: bool,
        reason: str,
    ) -> None:
        label = "accepted" if accepted else "rejected"
        self.state.last_wake_score = score
        self.state.last_wake_decision = f"{label} ({reason})"
        if accepted:
            self.state.accepted_wake_attempts += 1
        else:
            self.state.rejected_wake_attempts += 1
        self.state.wake_score_buckets[self._bucket_label(score)] += 1
        attempt = (
            f"{label.upper()} score={score:.2f} "
            f"reason={reason} text={self._truncate(transcript, limit=42)}"
        )
        self.state.recent_wake_attempts.appendleft(attempt)
        self.refresh()

    def log_event(self, event: str, refresh: bool = True) -> None:
        self.state.recent_events.appendleft(event)
        if refresh:
            self.refresh()

    def reset_turn(self) -> None:
        self.state.memory_hit_count = 0
        self.state.emitted_chunk_count = 0
        self.state.spoken_chunk_count = 0
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
                Panel(self._render_wake_debug(), title="Wake Debug", expand=True),
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
        table.add_row("Runtime", self._runtime_badge())
        table.add_row("Input", "text" if self.state.text_input_mode else "voice")
        table.add_row("Ollama", self.state.ollama_version)
        table.add_row("Recall hits", str(self.state.memory_hit_count))
        table.add_row("Chunks", f"{self.state.spoken_chunk_count}/{self.state.emitted_chunk_count}")
        if self.state.conversation_window_remaining is not None:
            table.add_row(
                "Window",
                f"{self.state.conversation_window_remaining:.1f}s",
            )
        if self.state.cooldown_remaining is not None:
            table.add_row(
                "Cooldown",
                f"{self.state.cooldown_remaining:.1f}s",
            )
        table.add_row("State", self.state.status_line)
        return table

    def _render_wake_debug(self) -> Table:
        table = Table.grid(padding=(0, 1))
        threshold = self.state.wake_score_threshold
        table.add_row(
            "Threshold",
            f"{threshold:.2f}" if threshold is not None else "n/a",
        )
        table.add_row(
            "Last score",
            f"{self.state.last_wake_score:.2f}"
            if self.state.last_wake_score is not None
            else "n/a",
        )
        table.add_row("Decision", self.state.last_wake_decision)
        table.add_row(
            "Accepted/Rejected",
            f"{self.state.accepted_wake_attempts}/{self.state.rejected_wake_attempts}",
        )
        histogram = ", ".join(
            f"{label}:{self.state.wake_score_buckets.get(label, 0)}"
            for label in ("<0.50", "0.50-0.74", "0.75-0.85", "0.86-0.94", "0.95+")
        )
        table.add_row("Score bins", histogram)
        if not self.state.recent_wake_attempts:
            table.add_row("Attempts", "No wake attempts yet.")
            return table
        for index, attempt in enumerate(self.state.recent_wake_attempts, start=1):
            table.add_row(f"Try {index}", attempt)
        return table

    def _render_latencies(self) -> Table:
        table = Table.grid(padding=(0, 1))
        if not self.state.latencies_ms:
            table.add_row("No timing data yet.")
            return table

        for label in ("capture", "stt", "router", "first_token", "tts", "stream_total", "total"):
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

    @staticmethod
    def _truncate(text: str, limit: int = 72) -> str:
        clean = " ".join(text.split())
        if len(clean) <= limit:
            return clean
        return clean[: limit - 3] + "..."

    def _runtime_badge(self) -> Text:
        if self.state.text_input_mode:
            return Text("TEXT", style="bold cyan")
        if self.state.runtime_mode == "turn-based":
            return Text("TURN-BASED", style="bold yellow")
        return Text("CONTINUOUS", style="bold green")

    @staticmethod
    def _bucket_label(score: float) -> str:
        if score < 0.50:
            return "<0.50"
        if score < 0.75:
            return "0.50-0.74"
        if score < 0.86:
            return "0.75-0.85"
        if score < 0.95:
            return "0.86-0.94"
        return "0.95+"
