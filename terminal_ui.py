from __future__ import annotations

from collections import Counter
from collections import deque
from dataclasses import dataclass, field
import threading
from time import perf_counter

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
    last_error: str = ""
    transcript: str = ""
    response: str = ""
    invocation_path: str = "chat-only"
    invocation_summary: str = "No invocation path yet."
    current_tool_status: str = "No backend tool used."
    last_tool_result: str = ""
    memory_hit_count: int = 0
    emitted_chunk_count: int = 0
    spoken_chunk_count: int = 0
    emitted_char_count: int = 0
    spoken_char_count: int = 0
    last_emitted_chunk: str = ""
    last_spoken_chunk: str = ""
    playback_gap_count: int = 0
    tail_merge_count: int = 0
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
    wake_score_total: float = 0.0
    wake_rejection_reasons: Counter[str] = field(default_factory=Counter)
    wake_guidance: str = "Say the wake phrase, pause briefly, then speak your request."
    wake_score_buckets: Counter[str] = field(default_factory=Counter)


class TerminalUI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.console = Console()
        self.state = UIState()
        self._state_lock = threading.RLock()
        self._turn_started_at = perf_counter()
        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=4,
            transient=False,
        )

    def start(self) -> None:
        with self._state_lock:
            self.live.start()
            self._refresh_locked()

    def stop(self) -> None:
        with self._state_lock:
            self.live.stop()

    def refresh(self) -> None:
        with self._state_lock:
            self._refresh_locked()

    def _refresh_locked(self) -> None:
        self.live.update(self._render())

    def set_connection(self, version: str, text_input_mode: bool) -> None:
        with self._state_lock:
            self.state.ollama_version = version
            self.state.text_input_mode = text_input_mode
            self.state.wake_score_threshold = self.settings.wake_match_score_threshold
            self.state.wake_guidance = self._default_wake_guidance()
            self.state.status_line = f"{self.settings.app_name} is ready. Press Ctrl+C to stop."
            self.state.mode = "ready"
            self.log_event(f"Connected to Ollama {version}", refresh=False)
            self._refresh_locked()

    def set_runtime_mode(self, runtime_mode: str) -> None:
        with self._state_lock:
            self.state.runtime_mode = runtime_mode
            self._refresh_locked()

    def show_startup_failure(self, detail: str) -> None:
        with self._state_lock:
            self.state.mode = "startup_error"
            self.state.status_line = detail
            self.state.last_error = detail
            self.log_event(detail, refresh=False)
            self._refresh_locked()

    def show_dependency_error(self, mode: str, detail: str) -> None:
        with self._state_lock:
            self.state.mode = mode
            self.state.status_line = detail
            self.state.last_error = detail
            self.log_event(detail, refresh=False)
            self._refresh_locked()

    def set_mode(self, mode: str, status_line: str | None = None) -> None:
        with self._state_lock:
            self.state.mode = mode
            if status_line is not None:
                self.state.status_line = status_line
            self._refresh_locked()

    def set_transcript(self, transcript: str) -> None:
        with self._state_lock:
            self.state.transcript = transcript
            self._refresh_locked()

    def set_response(self, response: str) -> None:
        with self._state_lock:
            self.state.response = response
            self._refresh_locked()

    def set_invocation(self, path: str, summary: str) -> None:
        with self._state_lock:
            self.state.invocation_path = path
            self.state.invocation_summary = summary
            self._refresh_locked()

    def record_tool_activity(self, tool_name: str, stage: str, detail: str) -> None:
        with self._state_lock:
            if stage == "selected":
                self.state.current_tool_status = f"{tool_name} selected"
                event = f"Tool selected: {tool_name}"
            elif stage == "running":
                self.state.current_tool_status = f"{tool_name} running"
                event = f"Tool running: {tool_name}"
            elif stage == "succeeded":
                self.state.current_tool_status = f"{tool_name} succeeded"
                self.state.last_tool_result = detail
                event = f"Tool succeeded: {detail}"
            elif stage == "failed":
                self.state.current_tool_status = f"{tool_name} failed"
                self.state.last_tool_result = detail
                event = f"Tool failed: {detail}"
            elif stage == "limit_reached":
                self.state.current_tool_status = "tool limit reached"
                self.state.last_tool_result = detail
                event = f"Tool limit reached: {detail}"
            else:
                self.state.current_tool_status = f"{tool_name} {stage}"
                event = f"Tool update: {tool_name} {stage}"
            self.log_event(event, refresh=False)
            self._refresh_locked()

    def set_memory_hits(self, count: int) -> None:
        with self._state_lock:
            self.state.memory_hit_count = count
            self._refresh_locked()

    def record_latency(self, label: str, seconds: float) -> None:
        with self._state_lock:
            self.state.latencies_ms[label] = seconds * 1000
            self._refresh_locked()

    def add_saved_items(self, saved_items: list[str]) -> None:
        with self._state_lock:
            for item in saved_items:
                self.state.recent_saves.appendleft(item)
                self.log_event(f"Saved memory: {item}", refresh=False)
            self._refresh_locked()

    def record_emitted_chunk(self, chunk: str) -> None:
        with self._state_lock:
            self.state.emitted_chunk_count += 1
            self.state.emitted_char_count += len(chunk)
            self.state.last_emitted_chunk = chunk
            self.log_event(
                f"Emitted speech chunk {self.state.emitted_chunk_count}: {self._truncate(chunk)}",
                refresh=False,
            )
            self._refresh_locked()

    def record_spoken_chunk(self, chunk: str) -> None:
        with self._state_lock:
            if self.state.spoken_chunk_count == 0:
                self.state.latencies_ms["first_spoken"] = (
                    perf_counter() - self._turn_started_at
                ) * 1000
            self.state.spoken_chunk_count += 1
            self.state.spoken_char_count += len(chunk)
            self.state.last_spoken_chunk = chunk
            self.log_event(
                f"Spoke chunk {self.state.spoken_chunk_count}: {self._truncate(chunk)}",
                refresh=False,
            )
            self._refresh_locked()

    def record_playback_gap(self) -> None:
        with self._state_lock:
            self.state.playback_gap_count += 1
            self.log_event("Playback buffer ran low; waiting on the next streamed chunk.", refresh=False)
            self._refresh_locked()

    def record_tail_merge(self) -> None:
        with self._state_lock:
            self.state.tail_merge_count += 1
            self.log_event("Merged a short trailing speech tail into the previous chunk.", refresh=False)
            self._refresh_locked()

    def set_conversation_window_remaining(self, seconds: float | None) -> None:
        with self._state_lock:
            self.state.conversation_window_remaining = seconds
            self._refresh_locked()

    def set_cooldown_remaining(self, seconds: float | None) -> None:
        with self._state_lock:
            self.state.cooldown_remaining = seconds
            self._refresh_locked()

    def set_wake_guidance(self, message: str) -> None:
        with self._state_lock:
            self.state.wake_guidance = message
            self._refresh_locked()

    def record_wake_attempt(
        self,
        transcript: str,
        score: float,
        accepted: bool,
        reason: str,
    ) -> None:
        with self._state_lock:
            label = "accepted" if accepted else "rejected"
            self.state.last_wake_score = score
            self.state.last_wake_decision = f"{label} ({reason})"
            if accepted:
                self.state.accepted_wake_attempts += 1
            else:
                self.state.rejected_wake_attempts += 1
                self.state.wake_rejection_reasons[reason] += 1
            self.state.wake_score_total += score
            self.state.wake_score_buckets[self._bucket_label(score)] += 1
            attempt = (
                f"{label.upper()} score={score:.2f} "
                f"reason={reason} text={self._truncate(transcript, limit=42)}"
            )
            self.state.recent_wake_attempts.appendleft(attempt)
            self._refresh_locked()

    def log_event(self, event: str, refresh: bool = True) -> None:
        with self._state_lock:
            self.state.recent_events.appendleft(event)
            if refresh:
                self._refresh_locked()

    def reset_turn(self) -> None:
        with self._state_lock:
            self._turn_started_at = perf_counter()
            self.state.memory_hit_count = 0
            self.state.emitted_chunk_count = 0
            self.state.spoken_chunk_count = 0
            self.state.emitted_char_count = 0
            self.state.spoken_char_count = 0
            self.state.last_emitted_chunk = ""
            self.state.last_spoken_chunk = ""
            self.state.playback_gap_count = 0
            self.state.tail_merge_count = 0
            self.state.latencies_ms = {}
            self.state.transcript = ""
            self.state.response = ""
            self.state.invocation_path = "chat-only"
            self.state.invocation_summary = "Awaiting invocation decision."
            self.state.current_tool_status = "No backend tool used."
            self.state.last_tool_result = ""
            self._refresh_locked()

    def prompt_text(self) -> str:
        with self._state_lock:
            self.live.stop()
        try:
            return self.console.input("\nYou> ").strip()
        finally:
            with self._state_lock:
                self.live.start()
                self._refresh_locked()

    def _render(self) -> Group:
        top_row = Columns(
            [
                Panel(
                    self._render_status(),
                    title="Status",
                    expand=True,
                    border_style=self._status_panel_style(),
                ),
                Panel(
                    self._render_latencies(),
                    title="Latencies",
                    expand=True,
                    border_style="magenta",
                ),
                Panel(
                    self._render_wake_debug(),
                    title="Wake Debug",
                    expand=True,
                    border_style="cyan",
                ),
            ],
            expand=True,
        )
        middle_row = Columns(
            [
                Panel(
                    self._render_transcript(),
                    title="Transcript",
                    expand=True,
                    border_style="bright_cyan",
                ),
                Panel(
                    self._render_response(),
                    title="Response",
                    expand=True,
                    border_style="bright_green",
                ),
            ],
            expand=True,
        )
        bottom_row = Columns(
            [
                Panel(
                    self._render_saves(),
                    title="Recent Memory Saves",
                    expand=True,
                    border_style="yellow",
                ),
                Panel(
                    self._render_events(),
                    title="Recent Turn Events",
                    expand=True,
                    border_style="white",
                ),
            ],
            expand=True,
        )
        return Group(top_row, middle_row, bottom_row)

    def _render_status(self) -> Table:
        table = Table.grid(padding=(0, 1))
        queue_backlog = max(0, self.state.emitted_chunk_count - self.state.spoken_chunk_count)
        average_emitted_chunk_chars = 0
        if self.state.emitted_chunk_count:
            average_emitted_chunk_chars = round(
                self.state.emitted_char_count / self.state.emitted_chunk_count
            )
        table.add_row("Assistant", Text(self.settings.app_name, style="bold white"))
        table.add_row("Mode", self._mode_badge())
        table.add_row("Runtime", self._runtime_badge())
        table.add_row(
            "Input",
            Text("text" if self.state.text_input_mode else "voice", style="bold cyan"),
        )
        table.add_row("Ollama", Text(self.state.ollama_version, style="bold white"))
        table.add_row(
            "Voice profile",
            Text(
                "practical" if self.settings.practical_voice_mode else "default",
                style="bold green" if self.settings.practical_voice_mode else "bright_blue",
            ),
        )
        table.add_row("Path", self._invocation_badge())
        table.add_row(
            "Tool",
            self._tool_status_text(),
        )
        table.add_row(
            "Tool result",
            Text(self.state.last_tool_result or "n/a", style="white", overflow="fold"),
        )
        table.add_row(
            "Chunks",
            Text(
                f"{self.state.spoken_chunk_count}/{self.state.emitted_chunk_count} (queue {queue_backlog})",
                style="bold green" if queue_backlog == 0 else "bold yellow",
            ),
        )
        table.add_row(
            "Avg chunk",
            Text(
                f"{average_emitted_chunk_chars} chars" if average_emitted_chunk_chars else "n/a",
                style="bright_magenta",
            ),
        )
        table.add_row(
            "Continuity",
            Text(
                f"tail merges {self.state.tail_merge_count}, buffer gaps {self.state.playback_gap_count}",
                style="bright_yellow"
                if self.state.playback_gap_count
                else "green",
            ),
        )
        table.add_row(
            "Transcript",
            Text(f"{len(self.state.transcript)} chars", style="bright_cyan"),
        )
        table.add_row(
            "Response",
            Text(f"{len(self.state.response)} chars", style="bright_green"),
        )
        table.add_row(
            "Last out",
            Text(
                self._truncate(self.state.last_emitted_chunk, limit=32) or "n/a",
                style="yellow",
            ),
        )
        table.add_row(
            "Last said",
            Text(
                self._truncate(self.state.last_spoken_chunk, limit=32) or "n/a",
                style="green",
            ),
        )
        if self.state.conversation_window_remaining is not None:
            table.add_row(
                "Window",
                Text(f"{self.state.conversation_window_remaining:.1f}s", style="bold cyan"),
            )
        if self.state.cooldown_remaining is not None:
            table.add_row(
                "Cooldown",
                Text(f"{self.state.cooldown_remaining:.1f}s", style="bold yellow"),
            )
        if self.state.last_error:
            table.add_row(
                "Last error",
                Text(self._truncate(self.state.last_error, limit=48), style="bold red"),
            )
        table.add_row("State", self._status_text())
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
            Text(
                f"{self.state.last_wake_score:.2f}"
                if self.state.last_wake_score is not None
                else "n/a",
                style="bold cyan",
            ),
        )
        table.add_row("Decision", self._wake_decision_text())
        table.add_row(
            "Accepted/Rejected",
            f"{self.state.accepted_wake_attempts}/{self.state.rejected_wake_attempts}",
        )
        table.add_row("Success rate", self._wake_success_rate_text())
        table.add_row("Average score", self._wake_average_score_text())
        histogram = ", ".join(
            f"{label}:{self.state.wake_score_buckets.get(label, 0)}"
            for label in ("<0.50", "0.50-0.74", "0.75-0.85", "0.86-0.94", "0.95+")
        )
        table.add_row("Score bins", histogram)
        table.add_row("Rejected by", self._wake_rejection_reason_text())
        table.add_row("Guidance", Text(self.state.wake_guidance, style="white", overflow="fold"))
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

        for label in (
            "capture",
            "stt",
            "router",
            "first_token",
            "first_spoken",
            "tts",
            "stream_total",
            "total",
        ):
            if label in self.state.latencies_ms:
                table.add_row(label, self._latency_text(self.state.latencies_ms[label]))
        return table

    def _render_transcript(self) -> Text:
        if not self.state.transcript:
            return Text("No transcript yet.", style="dim", overflow="fold")
        return Text(self.state.transcript, style="bright_cyan", overflow="fold")

    def _render_response(self) -> Text:
        if not self.state.response:
            return Text("No response yet.", style="dim", overflow="fold")
        return Text(self.state.response, style="bright_green", overflow="fold")

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

    def _mode_badge(self) -> Text:
        return Text(self.state.mode.upper(), style=self._mode_style())

    def _invocation_badge(self) -> Text:
        labels = {
            "explicit_save": ("EXPLICIT SAVE", "bold yellow"),
            "model_tool_call": ("NATURAL TOOL", "bold green"),
            "chat_only": ("CHAT ONLY", "bold cyan"),
        }
        label, style = labels.get(self.state.invocation_path, (self.state.invocation_path, "bold white"))
        return Text(label, style=style)

    def _tool_status_text(self) -> Text:
        status = self.state.current_tool_status
        if "failed" in status:
            style = "bold red"
        elif "succeeded" in status:
            style = "bold green"
        elif "running" in status or "selected" in status:
            style = "bold yellow"
        else:
            style = "dim"
        return Text(status, style=style, overflow="fold")

    def _mode_style(self) -> str:
        if "error" in self.state.mode:
            return "bold red"
        if self.state.mode in {"streaming", "wake_detected"}:
            return "bold bright_blue"
        if self.state.mode in {"speaking", "ready"}:
            return "bold green"
        if self.state.mode in {"thinking", "transcribing"}:
            return "bold yellow"
        if self.state.mode in {"passive_listening", "listening", "conversation_window"}:
            return "bold cyan"
        if self.state.mode == "cooldown":
            return "bold magenta"
        return "bold white"

    def _status_panel_style(self) -> str:
        if "error" in self.state.mode:
            return "red"
        if self.state.mode in {"speaking", "ready"}:
            return "green"
        if self.state.mode in {"streaming", "wake_detected"}:
            return "bright_blue"
        if self.state.mode in {"thinking", "transcribing"}:
            return "yellow"
        return "cyan"

    def _status_text(self) -> Text:
        return Text(
            self.state.status_line,
            style="bold red" if "error" in self.state.mode else "white",
            overflow="fold",
        )

    def _wake_decision_text(self) -> Text:
        style = "green" if self.state.last_wake_decision.startswith("accepted") else "yellow"
        return Text(self.state.last_wake_decision, style=style, overflow="fold")

    def _wake_success_rate_text(self) -> Text:
        total = self.state.accepted_wake_attempts + self.state.rejected_wake_attempts
        if total == 0:
            return Text("n/a", style="dim")
        success_rate = self.state.accepted_wake_attempts / total
        if success_rate >= 0.6:
            style = "green"
        elif success_rate >= 0.3:
            style = "yellow"
        else:
            style = "red"
        return Text(f"{success_rate * 100:.0f}%", style=style)

    def _wake_average_score_text(self) -> Text:
        total = self.state.accepted_wake_attempts + self.state.rejected_wake_attempts
        if total == 0:
            return Text("n/a", style="dim")
        average = self.state.wake_score_total / total
        return Text(f"{average:.2f}", style="bold cyan")

    def _wake_rejection_reason_text(self) -> Text:
        if not self.state.wake_rejection_reasons:
            return Text("n/a", style="dim")
        summary = ", ".join(
            f"{reason}:{count}"
            for reason, count in self.state.wake_rejection_reasons.most_common(3)
        )
        return Text(summary, style="yellow", overflow="fold")

    def _default_wake_guidance(self) -> str:
        guidance = f"Say '{self.settings.wake_phrase}', pause briefly, then speak your request."
        if self.settings.practical_voice_mode:
            return guidance + " Practical voice mode is on for a more forgiving wake scan."
        return guidance

    @staticmethod
    def _latency_text(milliseconds: float) -> Text:
        if milliseconds < 400:
            style = "green"
        elif milliseconds < 1200:
            style = "yellow"
        else:
            style = "red"
        return Text(f"{milliseconds:.1f} ms", style=style)

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
