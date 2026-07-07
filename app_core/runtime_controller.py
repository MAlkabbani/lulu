from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from time import perf_counter, sleep

from app_core.dependency_health import probe_dependency_health
from app_core.event_bus import EventBus
from app_core.runtime_models import RuntimeSnapshot, make_event
from audio_handler import (
    AudioCaptureError,
    AudioHandler,
    AudioTranscriptionError,
    MacOSTTS,
    PhraseChunker,
    TTSPlaybackError,
    audio_input_available,
    text_similarity,
)
from config import Settings, build_wake_guidance
from llm_router import HybridRouter
from memory_manager import MemoryManager
from ollama_client import OllamaClient, OllamaClientError
from terminal_ui import TerminalUI


@dataclass(frozen=True)
class RuntimeStopResult:
    stopped_cleanly: bool
    detail: str = ""


class RuntimeController:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        ollama_client: OllamaClient | None = None,
        memory_manager: MemoryManager | None = None,
        router: HybridRouter | None = None,
        audio_handler: AudioHandler | None = None,
        tts: MacOSTTS | None = None,
        ui: TerminalUI | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.ollama_client = ollama_client or OllamaClient(self.settings)
        self.memory_manager = memory_manager or MemoryManager(self.settings, self.ollama_client)
        self.router = router or HybridRouter(self.settings, self.ollama_client, self.memory_manager)
        self.audio_handler = audio_handler or AudioHandler(self.settings)
        self.tts = tts or MacOSTTS()
        self.ui = ui or TerminalUI(self.settings)
        self.event_bus = event_bus or EventBus()
        self.snapshot = RuntimeSnapshot()
        self.recent_spoken_chunks: deque[tuple[str, float]] = deque(maxlen=12)
        self._bootstrapped = False
        self._runtime_thread: threading.Thread | None = None
        self._runtime_stop_event: threading.Event | None = None
        self._wire_tts_callbacks()

    def _wire_tts_callbacks(self) -> None:
        def on_chunk_spoken(chunk: str) -> None:
            self.recent_spoken_chunks.append((chunk, perf_counter()))
            self.ui.record_spoken_chunk(chunk)
            self.event_bus.publish(
                make_event(
                    "tts.chunk_spoken",
                    chunk=chunk,
                    spoken_chunk_count=self.ui.state.spoken_chunk_count,
                    spoken_char_count=self.ui.state.spoken_char_count,
                )
            )

        def on_chunk_error(error: TTSPlaybackError) -> None:
            self.ui.show_dependency_error("tts_error", f"Speech playback failed: {error}")
            self.event_bus.publish(
                make_event("error.reported", mode="tts_error", detail=str(error))
            )

        self.tts.set_on_chunk_spoken(on_chunk_spoken)
        self.tts.set_on_chunk_error(on_chunk_error)

    def bootstrap(self) -> bool:
        ready = bootstrap_connection(
            self.ollama_client,
            self.ui,
            event_bus=self.event_bus,
        )
        self.snapshot = RuntimeSnapshot(
            mode=self.ui.state.mode,
            runtime_mode=self.ui.state.runtime_mode,
            status_line=self.ui.state.status_line,
            degraded=not ready,
            last_error=self.ui.state.last_error,
        )
        self._bootstrapped = ready
        return ready

    def current_dependency_health(self):  # noqa: ANN201
        available_models: list[str] = []
        try:
            available_models = self.ollama_client.list_models()
        except OllamaClientError:
            available_models = []
        return probe_dependency_health(
            self.settings,
            self.ollama_client,
            available_models=available_models,
            audio_input_available=audio_input_available(),
        )

    def get_diagnostics(self) -> dict[str, object]:
        state = self.ui.state
        runtime_active = self._runtime_thread is not None and self._runtime_thread.is_alive()
        return {
            "mode": state.mode,
            "runtime_mode": state.runtime_mode,
            "status_line": state.status_line,
            "last_error": state.last_error,
            "runtime_active": runtime_active,
            "transcript": state.transcript,
            "response": state.response,
            "invocation_summary": state.invocation_summary,
            "action_summary": state.action_summary,
            "current_tool_status": state.current_tool_status,
            "memory_hit_count": state.memory_hit_count,
            "emitted_chunk_count": state.emitted_chunk_count,
            "spoken_chunk_count": state.spoken_chunk_count,
            "emitted_char_count": state.emitted_char_count,
            "spoken_char_count": state.spoken_char_count,
            "last_emitted_chunk": state.last_emitted_chunk,
            "last_spoken_chunk": state.last_spoken_chunk,
            "playback_gap_count": state.playback_gap_count,
            "tail_merge_count": state.tail_merge_count,
            "recent_saves": list(state.recent_saves),
            "recent_events": list(state.recent_events),
            "recent_wake_attempts": list(state.recent_wake_attempts),
            "latencies_ms": dict(state.latencies_ms),
            "conversation_window_remaining": state.conversation_window_remaining,
            "cooldown_remaining": state.cooldown_remaining,
            "wake_guidance": state.wake_guidance,
            "last_wake_score": state.last_wake_score,
            "last_wake_decision": state.last_wake_decision,
            "wake_score_threshold": state.wake_score_threshold,
            "accepted_wake_attempts": state.accepted_wake_attempts,
            "rejected_wake_attempts": state.rejected_wake_attempts,
            "last_wake_confidence": state.last_wake_confidence,
            "last_wake_acoustic_score": state.last_wake_acoustic_score,
            "last_wake_dtw_score": state.last_wake_dtw_score,
            "last_wake_snr_db": state.last_wake_snr_db,
            "last_wake_feature_frames": state.last_wake_feature_frames,
        }

    def get_state(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            mode=self.ui.state.mode,
            runtime_mode=self.ui.state.runtime_mode,
            status_line=self.ui.state.status_line,
            degraded=bool(self.ui.state.last_error),
            last_error=self.ui.state.last_error,
        )

    def set_runtime_mode(self, runtime_mode: str) -> None:
        self.ui.set_runtime_mode(runtime_mode)
        self.snapshot = self.get_state()
        publish_runtime_state(self.ui, self.event_bus)

    def start_runtime(self, mode: str) -> RuntimeSnapshot:
        if mode not in {"continuous", "turn-based"}:
            set_ui_mode(
                self.ui,
                "runtime_error",
                f"Unsupported runtime mode: {mode}.",
                event_bus=self.event_bus,
            )
            self.snapshot = self.get_state()
            return self.snapshot
        if not self._bootstrapped and not self.bootstrap():
            self.snapshot = self.get_state()
            return self.snapshot
        stop_result = self._stop_background_runtime()
        if not stop_result.stopped_cleanly:
            self._report_runtime_stop_failure(stop_result.detail)
            self.snapshot = self.get_state()
            return self.snapshot
        if not self._ensure_voice_runtime_ready():
            self.snapshot = self.get_state()
            return self.snapshot
        self.set_runtime_mode(mode)
        self._start_background_runtime(mode)
        self.snapshot = self.get_state()
        return self.snapshot

    def stop_runtime(self) -> RuntimeSnapshot:
        stop_result = self._stop_background_runtime()
        if not stop_result.stopped_cleanly:
            self._report_runtime_stop_failure(stop_result.detail)
            self.snapshot = self.get_state()
            return self.snapshot
        set_conversation_window_remaining(self.ui, None, event_bus=self.event_bus)
        set_cooldown_remaining(self.ui, None, event_bus=self.event_bus)
        set_ui_mode(self.ui, "idle", "Runtime stopped.", event_bus=self.event_bus)
        self.snapshot = self.get_state()
        return self.snapshot

    def restart_runtime(self, mode: str | None = None) -> RuntimeSnapshot:
        selected_mode = mode or self.ui.state.runtime_mode
        self.stop_runtime()
        return self.start_runtime(selected_mode)

    def _start_background_runtime(self, mode: str) -> None:
        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._run_background_runtime,
            args=(mode, stop_event),
            daemon=True,
        )
        self._runtime_stop_event = stop_event
        self._runtime_thread = worker
        worker.start()

    def _stop_background_runtime(self) -> RuntimeStopResult:
        stop_event = self._runtime_stop_event
        worker = self._runtime_thread
        if stop_event is None or worker is None:
            self._runtime_stop_event = None
            self._runtime_thread = None
            return RuntimeStopResult(stopped_cleanly=True)
        stop_event.set()
        if worker.is_alive():
            worker.join(timeout=1.0)
        if worker.is_alive():
            detail = (
                "Runtime stop timed out; background voice worker is still running. "
                "Wait for the current audio turn to finish before restarting."
            )
            return RuntimeStopResult(stopped_cleanly=False, detail=detail)
        self._runtime_stop_event = None
        self._runtime_thread = None
        return RuntimeStopResult(stopped_cleanly=True)

    def _report_runtime_stop_failure(self, detail: str) -> None:
        self.ui.show_dependency_error("runtime_error", detail)
        publish_runtime_state(self.ui, self.event_bus)

    def _ensure_voice_runtime_ready(self) -> bool:
        try:
            self.audio_handler.ensure_transcription_ready()
        except AudioTranscriptionError as exc:
            handle_dependency_failure(
                self.ui,
                "stt_error",
                "Voice runtime preflight failed",
                exc,
                recoverable=False,
                event_bus=self.event_bus,
            )
            return False
        return True

    def _run_background_runtime(self, mode: str, stop_event: threading.Event) -> None:
        try:
            if mode == "turn-based":
                run_turn_based_voice_loop(
                    self.settings,
                    self.audio_handler,
                    self.router,
                    self.ollama_client,
                    self.tts,
                    self.ui,
                    stop_event=stop_event,
                    event_bus=self.event_bus,
                )
            elif mode == "continuous":
                run_continuous_voice_loop(
                    self.settings,
                    self.audio_handler,
                    self.router,
                    self.ollama_client,
                    self.tts,
                    self.ui,
                    self.recent_spoken_chunks,
                    stop_event=stop_event,
                    event_bus=self.event_bus,
                )
        except Exception as exc:
            handle_dependency_failure(
                self.ui,
                "runtime_error",
                "Background runtime failed",
                exc,
                recoverable=False,
                event_bus=self.event_bus,
            )
        finally:
            if self._runtime_stop_event is stop_event:
                self._runtime_stop_event = None
            if self._runtime_thread is threading.current_thread():
                self._runtime_thread = None

    def stop(self) -> None:
        self._stop_background_runtime()
        self.tts.close()
        self.ui.stop()

    def run(
        self,
        *,
        turn_based: bool,
        bootstrap_fn: Callable[[OllamaClient, TerminalUI], bool] | None = None,
    ) -> None:
        self.ui.start()
        try:
            bootstrap_call = bootstrap_fn or (
                lambda ollama_client, ui: bootstrap_connection(
                    ollama_client,
                    ui,
                    event_bus=self.event_bus,
                )
            )
            if not bootstrap_call(self.ollama_client, self.ui):
                return
            if turn_based or not self.settings.continuous_listening_enabled:
                self.ui.set_runtime_mode("turn-based")
                if turn_based:
                    self.ui.log_event("Turn-based troubleshooting mode enabled.")
                run_turn_based_voice_loop(
                    self.settings,
                    self.audio_handler,
                    self.router,
                    self.ollama_client,
                    self.tts,
                    self.ui,
                    event_bus=self.event_bus,
                )
            else:
                self.ui.set_runtime_mode("continuous")
                run_continuous_voice_loop(
                    self.settings,
                    self.audio_handler,
                    self.router,
                    self.ollama_client,
                    self.tts,
                    self.ui,
                    self.recent_spoken_chunks,
                    event_bus=self.event_bus,
                )
        finally:
            self.tts.close()
            self.ui.stop()


def publish_runtime_state(
    ui: TerminalUI,
    event_bus: EventBus | None,
    **extra_payload: object,
) -> None:
    if event_bus is None:
        return
    event_bus.publish(
        make_event(
            "runtime.state_changed",
            mode=ui.state.mode,
            runtime_mode=ui.state.runtime_mode,
            status_line=ui.state.status_line,
            conversation_window_remaining=ui.state.conversation_window_remaining,
            cooldown_remaining=ui.state.cooldown_remaining,
            wake_guidance=ui.state.wake_guidance,
            accepted_wake_attempts=ui.state.accepted_wake_attempts,
            rejected_wake_attempts=ui.state.rejected_wake_attempts,
            last_wake_score=ui.state.last_wake_score,
            last_wake_decision=ui.state.last_wake_decision,
            **extra_payload,
        )
    )


def set_ui_mode(
    ui: TerminalUI,
    mode: str,
    status_line: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    ui.set_mode(mode, status_line)
    publish_runtime_state(ui, event_bus)


def set_conversation_window_remaining(
    ui: TerminalUI,
    seconds: float | None,
    *,
    event_bus: EventBus | None = None,
) -> None:
    ui.set_conversation_window_remaining(seconds)
    publish_runtime_state(ui, event_bus)


def set_cooldown_remaining(
    ui: TerminalUI,
    seconds: float | None,
    *,
    event_bus: EventBus | None = None,
) -> None:
    ui.set_cooldown_remaining(seconds)
    publish_runtime_state(ui, event_bus)


def set_wake_guidance(
    ui: TerminalUI,
    message: str,
    *,
    event_bus: EventBus | None = None,
) -> None:
    ui.set_wake_guidance(message)
    if event_bus is not None:
        event_bus.publish(make_event("wake.guidance_updated", guidance=ui.state.wake_guidance))
    publish_runtime_state(ui, event_bus)


def set_wake_signal_metrics(
    ui: TerminalUI,
    *,
    confidence: float | None,
    threshold: float | None,
    acoustic_score: float | None,
    dtw_score: float | None,
    snr_db: float | None,
    feature_frames: int,
    event_bus: EventBus | None = None,
) -> None:
    ui.set_wake_signal_metrics(
        confidence=confidence,
        threshold=threshold,
        acoustic_score=acoustic_score,
        dtw_score=dtw_score,
        snr_db=snr_db,
        feature_frames=feature_frames,
    )
    if event_bus is not None:
        event_bus.publish(
            make_event(
                "wake.signal_metrics",
                confidence=confidence,
                threshold=threshold,
                acoustic_score=acoustic_score,
                dtw_score=dtw_score,
                snr_db=snr_db,
                feature_frames=feature_frames,
            )
        )


def record_wake_attempt(
    ui: TerminalUI,
    *,
    transcript: str,
    score: float,
    accepted: bool,
    reason: str | None,
    event_bus: EventBus | None = None,
) -> None:
    ui.record_wake_attempt(
        transcript=transcript,
        score=score,
        accepted=accepted,
        reason=reason,
    )
    if event_bus is not None:
        event_bus.publish(
            make_event(
                "wake.attempt",
                transcript=transcript,
                score=score,
                accepted=accepted,
                reason=reason,
                accepted_wake_attempts=ui.state.accepted_wake_attempts,
                rejected_wake_attempts=ui.state.rejected_wake_attempts,
                last_wake_decision=ui.state.last_wake_decision,
            )
        )


def record_latency(
    ui: TerminalUI,
    label: str,
    seconds: float,
    *,
    event_bus: EventBus | None = None,
) -> None:
    ui.record_latency(label, seconds)
    if event_bus is not None:
        event_bus.publish(
            make_event(
                "latency.snapshot",
                label=label,
                milliseconds=ui.state.latencies_ms.get(label, 0.0),
                latencies_ms=dict(ui.state.latencies_ms),
            )
        )


def run_turn_based_voice_loop(
    settings: Settings,
    audio_handler: AudioHandler,
    router: HybridRouter,
    ollama_client: OllamaClient,
    tts: MacOSTTS,
    ui: TerminalUI,
    stop_event: threading.Event | None = None,
    *,
    event_bus: EventBus | None = None,
) -> None:
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        set_conversation_window_remaining(ui, None, event_bus=event_bus)
        set_cooldown_remaining(ui, None, event_bus=event_bus)
        set_ui_mode(ui, "listening", "Listening for speech...", event_bus=event_bus)
        ui.log_event("Listening for speech.")
        audio, capture_failed = capture_audio(
            audio_handler.record_until_silence, ui, event_bus=event_bus
        )
        if capture_failed:
            set_ui_mode(ui, "idle", "Waiting for microphone recovery.", event_bus=event_bus)
            continue
        if audio is None:
            ui.log_event("No speech detected during capture.")
            set_ui_mode(ui, "idle", "No speech detected. Waiting again.", event_bus=event_bus)
            continue

        transcript = transcribe_audio(audio_handler, ui, audio, event_bus=event_bus)
        if not transcript:
            ui.log_event("Transcript was empty.")
            set_ui_mode(ui, "idle", "No transcript captured. Waiting again.", event_bus=event_bus)
            continue

        process_transcript_turn(
            transcript=transcript,
            settings=settings,
            router=router,
            ollama_client=ollama_client,
            tts=tts,
            ui=ui,
            event_bus=event_bus,
        )


def run_continuous_voice_loop(
    settings: Settings,
    audio_handler: AudioHandler,
    router: HybridRouter,
    ollama_client: OllamaClient,
    tts: MacOSTTS,
    ui: TerminalUI,
    recent_spoken_chunks: deque[tuple[str, float]],
    stop_event: threading.Event | None = None,
    *,
    event_bus: EventBus | None = None,
) -> None:
    conversation_deadline: float | None = None
    cooldown_until = 0.0
    last_assistant_reply = ""
    last_assistant_reply_at = 0.0
    set_wake_guidance(ui, build_wake_guidance(settings), event_bus=event_bus)
    ui.log_event(f"Passive listening enabled. Waiting for '{settings.wake_phrase}'.")

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        now = perf_counter()

        if cooldown_active(now, cooldown_until):
            remaining = cooldown_until - now
            set_cooldown_remaining(ui, remaining, event_bus=event_bus)
            set_conversation_window_remaining(
                ui,
                remaining_window(now, conversation_deadline),
                event_bus=event_bus,
            )
            set_ui_mode(
                ui,
                "cooldown",
                f"Wake detection cooling down after speech: {remaining:.1f}s",
                event_bus=event_bus,
            )
            sleep(min(0.1, remaining))
            continue

        set_cooldown_remaining(ui, None, event_bus=event_bus)

        if window_active(now, conversation_deadline):
            remaining = remaining_window(now, conversation_deadline)
            set_conversation_window_remaining(ui, remaining, event_bus=event_bus)
            set_ui_mode(
                ui,
                "conversation_window",
                f"Conversation window active: {remaining:.1f}s remaining",
                event_bus=event_bus,
            )
            audio, capture_failed = capture_audio(
                audio_handler.record_until_silence, ui, event_bus=event_bus
            )
            if capture_failed:
                sleep(0.2)
                continue
            if audio is None:
                if not window_active(perf_counter(), conversation_deadline):
                    ui.log_event("Conversation window expired. Returning to passive listening.")
                    conversation_deadline = None
                    set_conversation_window_remaining(ui, None, event_bus=event_bus)
                continue

            transcript = transcribe_audio(audio_handler, ui, audio, event_bus=event_bus)
            if not transcript:
                if not window_active(perf_counter(), conversation_deadline):
                    ui.log_event("Conversation window expired. Returning to passive listening.")
                    conversation_deadline = None
                    set_conversation_window_remaining(ui, None, event_bus=event_bus)
                continue

            process_transcript_turn(
                transcript=transcript,
                settings=settings,
                router=router,
                ollama_client=ollama_client,
                tts=tts,
                ui=ui,
                event_bus=event_bus,
            )
            if ui.state.response:
                last_assistant_reply = ui.state.response
                last_assistant_reply_at = perf_counter()
            conversation_deadline = next_conversation_deadline(settings)
            set_conversation_window_remaining(
                ui,
                settings.conversation_window_seconds,
                event_bus=event_bus,
            )
            cooldown_until = perf_counter() + settings.wake_cooldown_seconds
            continue

        if conversation_deadline is not None and not window_active(now, conversation_deadline):
            ui.log_event("Conversation window expired. Returning to passive listening.")
            conversation_deadline = None

        set_conversation_window_remaining(ui, None, event_bus=event_bus)
        set_ui_mode(
            ui,
            "passive_listening",
            f"Waiting for '{settings.wake_phrase}'...",
            event_bus=event_bus,
        )
        set_wake_guidance(ui, build_wake_guidance(settings), event_bus=event_bus)
        audio, capture_failed = capture_audio(
            audio_handler.record_wake_scan, ui, event_bus=event_bus
        )
        if capture_failed:
            sleep(0.2)
            continue
        if audio is None:
            continue

        wake_analysis = audio_handler.analyze_wake_audio(audio)
        record_latency(ui, "wake_audio", wake_analysis.latency_ms / 1000.0, event_bus=event_bus)
        set_wake_signal_metrics(
            ui,
            confidence=wake_analysis.confidence,
            threshold=wake_analysis.dynamic_threshold,
            acoustic_score=wake_analysis.acoustic_score,
            dtw_score=wake_analysis.dtw_score,
            snr_db=wake_analysis.snr_db,
            feature_frames=wake_analysis.feature_frames,
            event_bus=event_bus,
        )
        should_transcribe_wake_scan = wake_analysis.candidate or settings.practical_voice_mode
        if not should_transcribe_wake_scan:
            record_wake_attempt(
                ui,
                transcript="(acoustic prefilter rejected)",
                score=wake_analysis.confidence,
                accepted=False,
                reason=wake_analysis.reason,
                event_bus=event_bus,
            )
            set_wake_guidance(
                ui,
                "Reduce nearby noise or move closer to the mic, then say the wake phrase clearly.",
                event_bus=event_bus,
            )
            ui.log_event(
                "Rejected wake attempt acoustically before STT "
                f"(confidence {wake_analysis.confidence:.2f})."
            )
            continue
        if not wake_analysis.candidate and settings.practical_voice_mode:
            ui.log_event(
                "Acoustic wake confidence was low, but practical voice mode "
                "kept the STT wake scan enabled."
            )
            set_wake_guidance(
                ui,
                "Wake scan stayed in transcript mode because practical voice mode is enabled.",
                event_bus=event_bus,
            )

        if wake_analysis.fast_path_eligible and not recent_assistant_audio_guard_active(
            last_assistant_reply_at=last_assistant_reply_at,
            now=perf_counter(),
            settings=settings,
        ):
            wake_match = audio_handler.build_fast_path_wake_match(wake_analysis)
            record_wake_attempt(
                ui,
                transcript="(fast acoustic wake)",
                score=wake_match.score,
                accepted=True,
                reason=wake_match.reason,
                event_bus=event_bus,
            )
            ui.log_event(
                f"Accepted fast wake path score={wake_match.score:.2f} without waiting for STT."
            )
            ui.log_event(f"Wake phrase detected: {settings.wake_phrase}")
            set_wake_guidance(
                ui, "Wake matched quickly. Speak your request now.", event_bus=event_bus
            )
            conversation_deadline = next_conversation_deadline(settings)
            set_conversation_window_remaining(
                ui,
                settings.conversation_window_seconds,
                event_bus=event_bus,
            )
            set_ui_mode(
                ui,
                "conversation_window",
                "Conversation window active: "
                f"{settings.conversation_window_seconds:.1f}s remaining",
                event_bus=event_bus,
            )
            ui.log_event(
                f"Conversation window active: {settings.conversation_window_seconds:.1f}s remaining"
            )
            continue

        transcript = transcribe_audio(
            audio_handler,
            ui,
            wake_analysis.processed_audio,
            wake_scan=True,
            event_bus=event_bus,
        )
        if not transcript:
            set_wake_guidance(
                ui,
                "Try speaking a little louder or closer to the mic, "
                "then pause after the wake phrase.",
                event_bus=event_bus,
            )
            continue

        wake_match = audio_handler.match_wake_phrase(transcript, analysis=wake_analysis)
        if should_suppress_self_audio_echo(
            transcript=transcript,
            last_assistant_reply=last_assistant_reply,
            last_assistant_reply_at=last_assistant_reply_at,
            recent_spoken_chunks=recent_spoken_chunks,
            now=perf_counter(),
            settings=settings,
        ):
            record_wake_attempt(
                ui,
                transcript=transcript,
                score=wake_match.score,
                accepted=False,
                reason="self-audio-guard",
                event_bus=event_bus,
            )
            set_wake_guidance(
                ui,
                "Wait until Lulu finishes speaking, then say the wake phrase again.",
                event_bus=event_bus,
            )
            ui.log_event("Rejected wake attempt due to self-audio guard.")
            continue

        if not wake_match.matched:
            rejection_reason = wake_match.reason or "below-threshold"
            record_wake_attempt(
                ui,
                transcript=transcript,
                score=wake_match.score,
                accepted=False,
                reason=rejection_reason,
                event_bus=event_bus,
            )
            set_wake_guidance(
                ui,
                wake_rejection_guidance(rejection_reason, settings),
                event_bus=event_bus,
            )
            ui.log_event(
                f"Rejected wake attempt: {rejection_reason} (score {wake_match.score:.2f})."
            )
            continue

        record_wake_attempt(
            ui,
            transcript=transcript,
            score=wake_match.score,
            accepted=True,
            reason=wake_match.reason or "score-match",
            event_bus=event_bus,
        )
        ui.log_event(
            "Accepted wake attempt "
            f"score={wake_match.score:.2f}: "
            f"{wake_match.matched_prefix or settings.wake_phrase}"
        )
        ui.log_event(f"Wake phrase detected: {settings.wake_phrase}")
        set_wake_guidance(ui, "Wake matched. Speak your request now.", event_bus=event_bus)
        conversation_deadline = next_conversation_deadline(settings)
        set_conversation_window_remaining(
            ui,
            settings.conversation_window_seconds,
            event_bus=event_bus,
        )

        if wake_match.remainder:
            ui.log_event(f"Wake phrase carried inline request: {wake_match.remainder}")
            process_transcript_turn(
                transcript=wake_match.remainder,
                settings=settings,
                router=router,
                ollama_client=ollama_client,
                tts=tts,
                ui=ui,
                event_bus=event_bus,
            )
            if ui.state.response:
                last_assistant_reply = ui.state.response
                last_assistant_reply_at = perf_counter()
            conversation_deadline = next_conversation_deadline(settings)
            set_conversation_window_remaining(
                ui,
                settings.conversation_window_seconds,
                event_bus=event_bus,
            )
            cooldown_until = perf_counter() + settings.wake_cooldown_seconds
        else:
            set_ui_mode(
                ui,
                "conversation_window",
                "Conversation window active: "
                f"{settings.conversation_window_seconds:.1f}s remaining",
                event_bus=event_bus,
            )
            ui.log_event(
                f"Conversation window active: {settings.conversation_window_seconds:.1f}s remaining"
            )


def wake_rejection_response(reason: str, score: float, settings: Settings) -> str:
    phrase = settings.wake_phrase
    if reason == "acoustic-reject":
        return (
            "Wake rejected before transcription: the audio pattern did not "
            f"confidently match '{phrase}' "
            f"(confidence {score:.2f})."
        )
    if reason == "too-short":
        return f"Wake rejected: heard only a short fragment. Try saying '{phrase}' more fully."
    if reason == "self-audio-guard":
        return "Wake rejected: likely picked up Lulu's own voice."
    if reason == "below-threshold":
        return f"Wake rejected: it did not sound enough like '{phrase}' (score {score:.2f})."
    return f"Wake rejected: {reason} (score {score:.2f})"


def wake_rejection_guidance(reason: str, settings: Settings) -> str:
    phrase = settings.wake_phrase
    if reason == "acoustic-reject":
        return (
            "Reduce background noise if possible, move closer to the mic, "
            f"and say '{phrase}' in one short phrase."
        )
    if reason == "too-short":
        return f"Say '{phrase}' clearly and let the phrase finish before your request."
    if reason == "below-threshold":
        return (
            f"Try saying '{phrase}' first, pause briefly, then say the request in a second phrase."
        )
    if reason == "self-audio-guard":
        return "Wait until Lulu finishes speaking, then try the wake phrase again."
    return build_wake_guidance(settings)


def transcribe_audio(
    audio_handler: AudioHandler,
    ui: TerminalUI,
    audio,  # noqa: ANN001
    wake_scan: bool = False,
    *,
    event_bus: EventBus | None = None,
) -> str:
    if wake_scan:
        set_ui_mode(
            ui,
            "wake_detected",
            "Potential wake speech detected. Transcribing...",
            event_bus=event_bus,
        )
        ui.log_event("Passive wake scan captured speech.")
    else:
        set_ui_mode(ui, "transcribing", "Running MLX Whisper transcription...", event_bus=event_bus)
        ui.log_event("Captured audio. Starting transcription.")

    stt_start = perf_counter()
    try:
        settings = getattr(audio_handler, "settings", None)
        initial_prompt = settings.wake_phrase if wake_scan and settings is not None else None
        transcript = audio_handler.transcribe_audio(audio, initial_prompt=initial_prompt)
    except AudioTranscriptionError as exc:
        record_latency(ui, "stt", perf_counter() - stt_start, event_bus=event_bus)
        handle_dependency_failure(ui, "stt_error", "Transcription failed", exc, event_bus=event_bus)
        return ""
    record_latency(ui, "stt", perf_counter() - stt_start, event_bus=event_bus)
    if wake_scan and transcript:
        ui.set_transcript(transcript)
        set_wake_guidance(
            ui,
            "Checking whether the wake phrase matched what Whisper heard.",
            event_bus=event_bus,
        )
    if event_bus is not None and transcript:
        event_bus.publish(
            make_event("transcript.updated", transcript=transcript, wake_scan=wake_scan)
        )
    return transcript


def bootstrap_connection(
    ollama_client: OllamaClient,
    ui: TerminalUI,
    *,
    event_bus: EventBus | None = None,
) -> bool:
    try:
        version = ollama_client.healthcheck()
    except OllamaClientError as exc:
        ui.show_startup_failure(f"Ollama startup check failed: {exc}")
        if event_bus is not None:
            event_bus.publish(make_event("error.reported", mode="startup_error", detail=str(exc)))
        return False

    ui.set_connection(version=str(version.get("version", "unknown")))
    if event_bus is not None:
        publish_runtime_state(ui, event_bus, version=str(version.get("version", "unknown")))
    return True


def capture_audio(
    capture_fn: Callable[[], object | None],
    ui: TerminalUI,
    *,
    event_bus: EventBus | None = None,
) -> tuple[object | None, bool]:
    capture_start = perf_counter()
    try:
        audio = capture_fn()
    except AudioCaptureError as exc:
        record_latency(ui, "capture", perf_counter() - capture_start, event_bus=event_bus)
        handle_dependency_failure(
            ui, "capture_error", "Microphone capture failed", exc, event_bus=event_bus
        )
        return None, True

    record_latency(ui, "capture", perf_counter() - capture_start, event_bus=event_bus)
    return audio, False


def handle_dependency_failure(
    ui: TerminalUI,
    mode: str,
    prefix: str,
    exc: Exception,
    recoverable: bool = True,
    *,
    event_bus: EventBus | None = None,
) -> None:
    detail = str(exc).strip()
    message = f"{prefix}: {detail}" if detail else prefix
    if recoverable:
        ui.show_dependency_error(mode, message)
    else:
        ui.show_startup_failure(message)
    if event_bus is not None:
        event_bus.publish(make_event("error.reported", mode=mode, detail=message))


def process_transcript_turn(
    transcript: str,
    settings: Settings,
    router: HybridRouter,
    ollama_client: OllamaClient,
    tts: MacOSTTS,
    ui: TerminalUI,
    *,
    event_bus: EventBus | None = None,
) -> None:
    turn_start = perf_counter()
    ui.reset_turn()
    ui.set_transcript(transcript)
    ui.log_event(f"Transcript ready: {transcript}")
    if event_bus is not None:
        event_bus.publish(make_event("transcript.updated", transcript=transcript, wake_scan=False))
    set_ui_mode(ui, "thinking", "Querying memory and generating a reply...", event_bus=event_bus)
    ui.log_event("Running memory recall and router.")
    router_start = perf_counter()
    try:
        prepared = router.prepare_turn(transcript)
    except Exception as exc:
        record_latency(ui, "router", perf_counter() - router_start, event_bus=event_bus)
        record_latency(ui, "total", perf_counter() - turn_start, event_bus=event_bus)
        handle_dependency_failure(
            ui, "router_error", "Turn preparation failed", exc, event_bus=event_bus
        )
        return
    record_latency(ui, "router", perf_counter() - router_start, event_bus=event_bus)
    ui.set_memory_hits(len(prepared.memory_hits))
    ui.log_event(f"Retrieved {len(prepared.memory_hits)} memory hit(s).")
    ui.set_invocation(prepared.invocation_path, prepared.invocation_summary)
    ui.log_event(prepared.invocation_summary)
    if event_bus is not None:
        event_bus.publish(
            make_event(
                "router.invocation_updated",
                invocation_path=prepared.invocation_path,
                invocation_summary=prepared.invocation_summary,
                memory_hit_count=len(prepared.memory_hits),
                action_summary=ui.state.action_summary,
                current_tool_status=ui.state.current_tool_status,
            )
        )
    for trace in prepared.tool_traces:
        ui.record_tool_activity(trace.tool_name, trace.stage, trace.detail)
        if event_bus is not None:
            event_bus.publish(
                make_event(
                    "tool.activity",
                    tool_name=trace.tool_name,
                    stage=trace.stage,
                    detail=trace.detail,
                    action_summary=ui.state.action_summary,
                    current_tool_status=ui.state.current_tool_status,
                )
            )
    if prepared.saved_items:
        ui.add_saved_items(prepared.saved_items)
        if event_bus is not None:
            event_bus.publish(make_event("memory.saved", saved_items=list(prepared.saved_items)))
    if prepared.invocation_path == "explicit_save":
        ui.record_explicit_save("updated" in prepared.fixed_reply.lower())
        set_ui_mode(
            ui,
            "thinking",
            "Deterministic memory save accepted. Preparing spoken confirmation...",
            event_bus=event_bus,
        )
    elif prepared.tool_traces:
        final_tool_stage = prepared.tool_traces[-1].stage
        if final_tool_stage == "failed":
            set_ui_mode(
                ui,
                "thinking",
                "Backend action was rejected safely. Generating a reply...",
                event_bus=event_bus,
            )
        elif final_tool_stage == "limit_reached":
            set_ui_mode(
                ui,
                "thinking",
                "Backend action hit the configured tool limit. Generating a reply...",
                event_bus=event_bus,
            )
        else:
            set_ui_mode(
                ui,
                "thinking",
                "Backend action completed. Generating a reply...",
                event_bus=event_bus,
            )
    else:
        set_ui_mode(
            ui,
            "thinking",
            "No backend action requested. Generating a reply...",
            event_bus=event_bus,
        )

    if not prepared.fixed_reply and not prepared.final_messages:
        record_latency(ui, "total", perf_counter() - turn_start, event_bus=event_bus)
        ui.log_event("No spoken response was generated.")
        set_ui_mode(ui, "idle", "No spoken response generated.", event_bus=event_bus)
        return

    chunker = PhraseChunker(settings)
    response_parts: list[str] = []
    stream_start = perf_counter()
    speech_start: float | None = None
    first_token_recorded = False
    tts.start_turn()
    set_ui_mode(ui, "streaming", "Streaming response and speaking chunks...", event_bus=event_bus)

    if prepared.final_messages:
        ui.log_event("Streaming final response from Ollama.")
        stream_source = ollama_client.stream_chat(prepared.final_messages)
    else:
        ui.log_event("Streaming fixed reply through chunked TTS.")
        stream_source = iter([prepared.fixed_reply])

    try:
        for piece in stream_and_chunk(
            stream_source=stream_source,
            settings=settings,
            chunker=chunker,
            tts=tts,
            ui=ui,
            response_parts=response_parts,
            event_bus=event_bus,
        ):
            if not first_token_recorded and piece.strip():
                record_latency(
                    ui, "first_token", perf_counter() - stream_start, event_bus=event_bus
                )
                first_token_recorded = True
            if speech_start is None and ui.state.emitted_chunk_count > 0:
                speech_start = perf_counter()
    except Exception as exc:
        final_text = "".join(response_parts).strip()
        flushed_chunks = flush_chunker_tail(chunker=chunker, tts=tts, ui=ui, event_bus=event_bus)
        if flushed_chunks:
            ui.log_event(f"Flushed {flushed_chunks} buffered speech chunk(s) after stream failure.")
        if final_text:
            ui.set_response(final_text)
            ui.log_event("Preserved partial response text after stream failure.")
        tts.finish_turn()
        record_latency(ui, "stream_total", perf_counter() - stream_start, event_bus=event_bus)
        record_latency(ui, "total", perf_counter() - turn_start, event_bus=event_bus)
        handle_dependency_failure(
            ui, "stream_error", "Response streaming failed", exc, event_bus=event_bus
        )
        return

    final_text = "".join(response_parts).strip()
    if not final_text:
        record_latency(ui, "stream_total", perf_counter() - stream_start, event_bus=event_bus)
        record_latency(ui, "total", perf_counter() - turn_start, event_bus=event_bus)
        ui.log_event("No spoken response was generated.")
        set_ui_mode(ui, "idle", "No spoken response generated.", event_bus=event_bus)
        tts.finish_turn()
        return

    ui.set_response(final_text)
    if event_bus is not None:
        event_bus.publish(make_event("response.final", text=final_text))
    set_ui_mode(
        ui, "speaking", "Waiting for queued speech chunks to finish...", event_bus=event_bus
    )
    tts_errors = tts.finish_turn()
    if speech_start is not None:
        record_latency(ui, "tts", perf_counter() - speech_start, event_bus=event_bus)
    record_latency(ui, "stream_total", perf_counter() - stream_start, event_bus=event_bus)
    record_latency(ui, "total", perf_counter() - turn_start, event_bus=event_bus)
    if tts_errors:
        ui.log_event("Retained final text response despite TTS playback failure.")
        handle_dependency_failure(
            ui,
            "tts_error",
            "Speech playback completed with errors",
            tts_errors[0],
            event_bus=event_bus,
        )
        return
    ui.log_event("Finished streamed playback.")
    set_ui_mode(ui, "ready", "Turn complete. Waiting for the next turn.", event_bus=event_bus)


def stream_and_chunk(
    stream_source: Iterator[str],
    settings: Settings,
    chunker: PhraseChunker,
    tts: MacOSTTS,
    ui: TerminalUI,
    response_parts: list[str],
    *,
    event_bus: EventBus | None = None,
) -> Iterator[str]:
    playback_started = False
    pending_chunks: list[str] = []

    for piece in stream_source:
        if playback_started and len(pending_chunks) <= 1 and ui.state.emitted_chunk_count > 0:
            ui.record_playback_gap()
        response_parts.append(piece)
        ui.set_response("".join(response_parts).strip())
        if event_bus is not None and piece.strip():
            event_bus.publish(
                make_event("response.partial", text="".join(response_parts).strip(), piece=piece)
            )
        for chunk in chunker.push(piece):
            pending_chunks.append(chunk)
            if playback_started:
                emit_ready_chunks_with_lookahead(
                    tts=tts, ui=ui, pending_chunks=pending_chunks, event_bus=event_bus
                )
        if not playback_started and should_start_playback(
            settings=settings, pending_chunks=pending_chunks
        ):
            emit_ready_chunks_with_lookahead(
                tts=tts, ui=ui, pending_chunks=pending_chunks, event_bus=event_bus
            )
            playback_started = True
        yield piece

    pending_chunks.extend(chunker.finish())
    if merge_short_final_pending_tail(settings=settings, pending_chunks=pending_chunks):
        ui.record_tail_merge()
    emit_pending_chunks(tts=tts, ui=ui, pending_chunks=pending_chunks, event_bus=event_bus)


def flush_chunker_tail(
    *,
    chunker: PhraseChunker,
    tts: MacOSTTS,
    ui: TerminalUI,
    event_bus: EventBus | None = None,
) -> int:
    pending_chunks = list(chunker.finish())
    if merge_short_final_pending_tail(settings=ui.settings, pending_chunks=pending_chunks):
        ui.record_tail_merge()
    emit_pending_chunks(tts=tts, ui=ui, pending_chunks=pending_chunks, event_bus=event_bus)
    return len(pending_chunks)


def emit_pending_chunks(
    *,
    tts: MacOSTTS,
    ui: TerminalUI,
    pending_chunks: list[str],
    event_bus: EventBus | None = None,
) -> None:
    while pending_chunks:
        chunk = pending_chunks.pop(0)
        tts.enqueue_chunk(chunk)
        ui.record_emitted_chunk(chunk)
        if event_bus is not None:
            event_bus.publish(
                make_event(
                    "tts.chunk_emitted",
                    chunk=chunk,
                    emitted_chunk_count=ui.state.emitted_chunk_count,
                    emitted_char_count=ui.state.emitted_char_count,
                )
            )


def emit_ready_chunks_with_lookahead(
    *,
    tts: MacOSTTS,
    ui: TerminalUI,
    pending_chunks: list[str],
    event_bus: EventBus | None = None,
) -> None:
    while len(pending_chunks) > 1:
        chunk = pending_chunks.pop(0)
        tts.enqueue_chunk(chunk)
        ui.record_emitted_chunk(chunk)
        if event_bus is not None:
            event_bus.publish(
                make_event(
                    "tts.chunk_emitted",
                    chunk=chunk,
                    emitted_chunk_count=ui.state.emitted_chunk_count,
                    emitted_char_count=ui.state.emitted_char_count,
                )
            )


def should_start_playback(*, settings: Settings, pending_chunks: list[str]) -> bool:
    buffered_chars = sum(len(chunk) for chunk in pending_chunks)
    if buffered_chars >= settings.tts_stream_start_buffer_chars:
        return True
    return len(pending_chunks) >= max(1, settings.tts_stream_max_group_sentences)


def merge_short_final_pending_tail(*, settings: Settings, pending_chunks: list[str]) -> bool:
    if len(pending_chunks) < 2:
        return False
    last = pending_chunks[-1]
    previous = pending_chunks[-2]
    if len(last) > settings.tts_stream_tail_merge_chars:
        return False
    if (
        len(previous) + len(last) + 1
        > settings.tts_stream_max_chunk_chars + settings.tts_stream_tail_merge_overflow_chars
    ):
        return False
    pending_chunks[-2] = f"{previous} {last}".strip()
    pending_chunks.pop()
    return True


def next_conversation_deadline(settings: Settings) -> float:
    return perf_counter() + settings.conversation_window_seconds


def window_active(now: float, deadline: float | None) -> bool:
    return deadline is not None and now < deadline


def remaining_window(now: float, deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - now)


def cooldown_active(now: float, cooldown_until: float) -> bool:
    return now < cooldown_until


def recent_assistant_audio_guard_active(
    *, last_assistant_reply_at: float, now: float, settings: Settings
) -> bool:
    return (
        last_assistant_reply_at > 0.0
        and (now - last_assistant_reply_at) <= settings.self_audio_guard_seconds
    )


def should_suppress_self_audio_echo(
    *,
    transcript: str,
    last_assistant_reply: str,
    last_assistant_reply_at: float,
    recent_spoken_chunks: deque[tuple[str, float]],
    now: float,
    settings: Settings,
) -> bool:
    normalized_transcript = transcript.strip().lower()
    if not normalized_transcript:
        return False
    if recent_assistant_audio_guard_active(
        last_assistant_reply_at=last_assistant_reply_at,
        now=now,
        settings=settings,
    ):
        similarity = text_similarity(normalized_transcript, last_assistant_reply.strip().lower())
        if similarity >= settings.self_audio_similarity_threshold:
            return True
    for chunk, chunk_spoken_at in recent_spoken_chunks:
        if (now - chunk_spoken_at) > settings.self_audio_guard_seconds:
            continue
        similarity = text_similarity(normalized_transcript, chunk.strip().lower())
        if similarity >= settings.self_audio_similarity_threshold:
            return True
    return False
