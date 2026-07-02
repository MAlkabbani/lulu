from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable, Iterator
from time import perf_counter, sleep

from audio_handler import (
    AudioCaptureError,
    AudioHandler,
    AudioTranscriptionError,
    MacOSTTS,
    PhraseChunker,
    TTSPlaybackError,
    text_similarity,
)
from config import Settings
from llm_router import HybridRouter
from memory_manager import MemoryManager
from ollama_client import OllamaClient, OllamaClientError
from terminal_ui import TerminalUI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lulu VAIA local voice assistant")
    parser.add_argument(
        "--text-input",
        action="store_true",
        help="Use typed input instead of microphone capture for quick testing.",
    )
    parser.add_argument(
        "--turn-based",
        action="store_true",
        help="Temporarily disable continuous listening and use the older one-turn voice loop.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings()
    ollama_client = OllamaClient(settings)
    memory_manager = MemoryManager(settings, ollama_client)
    router = HybridRouter(settings, ollama_client, memory_manager)
    audio_handler = AudioHandler(settings)
    tts = MacOSTTS()
    ui = TerminalUI(settings)
    recent_spoken_chunks: deque[tuple[str, float]] = deque(maxlen=12)

    def on_chunk_spoken(chunk: str) -> None:
        recent_spoken_chunks.append((chunk, perf_counter()))
        ui.record_spoken_chunk(chunk)

    def on_chunk_error(error: TTSPlaybackError) -> None:
        ui.show_dependency_error("tts_error", f"Speech playback failed: {error}")

    tts.set_on_chunk_spoken(on_chunk_spoken)
    tts.set_on_chunk_error(on_chunk_error)

    ui.start()
    if not _bootstrap_connection(ollama_client, ui, args.text_input):
        return
    if args.text_input:
        ui.set_runtime_mode("text")
    elif args.turn_based or not settings.continuous_listening_enabled:
        ui.set_runtime_mode("turn-based")
    else:
        ui.set_runtime_mode("continuous")
    if args.turn_based:
        ui.log_event("Turn-based troubleshooting mode enabled.")

    try:
        if args.text_input:
            _run_text_loop(settings, router, ollama_client, tts, ui)
        elif settings.continuous_listening_enabled and not args.turn_based:
            _run_continuous_voice_loop(
                settings,
                audio_handler,
                router,
                ollama_client,
                tts,
                ui,
                recent_spoken_chunks,
            )
        else:
            _run_turn_based_voice_loop(settings, audio_handler, router, ollama_client, tts, ui)
    except KeyboardInterrupt:
        print("\nGoodbye.")
    except Exception as exc:
        _handle_dependency_failure(
            ui,
            "runtime_error",
            "Lulu stopped due to an unexpected runtime failure",
            exc,
            recoverable=False,
        )
    finally:
        tts.close()
        ui.stop()


def _run_text_loop(
    settings: Settings,
    router: HybridRouter,
    ollama_client: OllamaClient,
    tts: MacOSTTS,
    ui: TerminalUI,
) -> None:
    while True:
        ui.set_mode("idle", "Waiting for the next text turn.")
        transcript = ui.prompt_text()
        if not transcript:
            ui.log_event("Text input was empty.")
            continue
        ui.record_latency("capture", 0.0)
        ui.log_event("Accepted text input.")
        _process_transcript_turn(
            transcript=transcript,
            settings=settings,
            router=router,
            ollama_client=ollama_client,
            tts=tts,
            ui=ui,
        )


def _run_turn_based_voice_loop(
    settings: Settings,
    audio_handler: AudioHandler,
    router: HybridRouter,
    ollama_client: OllamaClient,
    tts: MacOSTTS,
    ui: TerminalUI,
) -> None:
    while True:
        ui.set_conversation_window_remaining(None)
        ui.set_cooldown_remaining(None)
        ui.set_mode("listening", "Listening for speech...")
        ui.log_event("Listening for speech.")
        audio, capture_failed = _capture_audio(audio_handler.record_until_silence, ui)
        if capture_failed:
            ui.set_mode("idle", "Waiting for microphone recovery.")
            continue
        if audio is None:
            ui.log_event("No speech detected during capture.")
            ui.set_mode("idle", "No speech detected. Waiting again.")
            continue

        transcript = _transcribe_audio(audio_handler, ui, audio)
        if not transcript:
            ui.log_event("Transcript was empty.")
            ui.set_mode("idle", "No transcript captured. Waiting again.")
            continue

        _process_transcript_turn(
            transcript=transcript,
            settings=settings,
            router=router,
            ollama_client=ollama_client,
            tts=tts,
            ui=ui,
        )


def _run_continuous_voice_loop(
    settings: Settings,
    audio_handler: AudioHandler,
    router: HybridRouter,
    ollama_client: OllamaClient,
    tts: MacOSTTS,
    ui: TerminalUI,
    recent_spoken_chunks: deque[tuple[str, float]],
) -> None:
    conversation_deadline: float | None = None
    cooldown_until = 0.0
    last_assistant_reply = ""
    last_assistant_reply_at = 0.0
    ui.log_event(f"Passive listening enabled. Waiting for '{settings.wake_phrase}'.")

    while True:
        now = perf_counter()

        if _cooldown_active(now, cooldown_until):
            remaining = cooldown_until - now
            ui.set_cooldown_remaining(remaining)
            ui.set_conversation_window_remaining(_remaining_window(now, conversation_deadline))
            ui.set_mode(
                "cooldown",
                f"Wake detection cooling down after speech: {remaining:.1f}s",
            )
            sleep(min(0.1, remaining))
            continue

        ui.set_cooldown_remaining(None)

        if _window_active(now, conversation_deadline):
            remaining = _remaining_window(now, conversation_deadline)
            ui.set_conversation_window_remaining(remaining)
            ui.set_mode(
                "conversation_window",
                f"Conversation window active: {remaining:.1f}s remaining",
            )
            audio, capture_failed = _capture_audio(audio_handler.record_until_silence, ui)
            if capture_failed:
                sleep(0.2)
                continue
            if audio is None:
                if not _window_active(perf_counter(), conversation_deadline):
                    ui.log_event("Conversation window expired. Returning to passive listening.")
                    conversation_deadline = None
                    ui.set_conversation_window_remaining(None)
                continue

            transcript = _transcribe_audio(audio_handler, ui, audio)
            if not transcript:
                if not _window_active(perf_counter(), conversation_deadline):
                    ui.log_event("Conversation window expired. Returning to passive listening.")
                    conversation_deadline = None
                    ui.set_conversation_window_remaining(None)
                continue

            _process_transcript_turn(
                transcript=transcript,
                settings=settings,
                router=router,
                ollama_client=ollama_client,
                tts=tts,
                ui=ui,
            )
            if ui.state.response:
                last_assistant_reply = ui.state.response
                last_assistant_reply_at = perf_counter()
            conversation_deadline = _next_conversation_deadline(settings)
            ui.set_conversation_window_remaining(settings.conversation_window_seconds)
            cooldown_until = perf_counter() + settings.wake_cooldown_seconds
            continue

        if conversation_deadline is not None and not _window_active(now, conversation_deadline):
            ui.log_event("Conversation window expired. Returning to passive listening.")
            conversation_deadline = None

        ui.set_conversation_window_remaining(None)
        ui.set_mode("passive_listening", f"Waiting for '{settings.wake_phrase}'...")
        audio, capture_failed = _capture_audio(audio_handler.record_wake_scan, ui)
        if capture_failed:
            sleep(0.2)
            continue
        if audio is None:
            continue

        transcript = _transcribe_audio(audio_handler, ui, audio, wake_scan=True)
        if not transcript:
            ui.set_response("Wake scan produced no transcript. Listening again...")
            continue

        wake_match = audio_handler.match_wake_phrase(transcript)
        if _should_suppress_self_audio_echo(
            transcript=transcript,
            last_assistant_reply=last_assistant_reply,
            last_assistant_reply_at=last_assistant_reply_at,
            recent_spoken_chunks=recent_spoken_chunks,
            now=perf_counter(),
            settings=settings,
        ):
            ui.record_wake_attempt(
                transcript=transcript,
                score=wake_match.score,
                accepted=False,
                reason="self-audio-guard",
            )
            ui.set_response("Wake rejected: self-audio-guard")
            ui.log_event("Rejected wake attempt due to self-audio guard.")
            continue

        if not wake_match.matched:
            rejection_reason = wake_match.reason or "below-threshold"
            ui.record_wake_attempt(
                transcript=transcript,
                score=wake_match.score,
                accepted=False,
                reason=rejection_reason,
            )
            ui.set_response(
                f"Wake rejected: {rejection_reason} (score {wake_match.score:.2f})"
            )
            ui.log_event("Rejected wake attempt below threshold.")
            continue

        ui.record_wake_attempt(
            transcript=transcript,
            score=wake_match.score,
            accepted=True,
            reason=wake_match.reason or "score-match",
        )
        ui.log_event(
            f"Accepted wake attempt score={wake_match.score:.2f}: {wake_match.matched_prefix or settings.wake_phrase}"
        )
        ui.log_event(f"Wake phrase detected: {settings.wake_phrase}")
        conversation_deadline = _next_conversation_deadline(settings)
        ui.set_conversation_window_remaining(settings.conversation_window_seconds)

        if wake_match.remainder:
            ui.log_event(f"Wake phrase carried inline request: {wake_match.remainder}")
            _process_transcript_turn(
                transcript=wake_match.remainder,
                settings=settings,
                router=router,
                ollama_client=ollama_client,
                tts=tts,
                ui=ui,
            )
            if ui.state.response:
                last_assistant_reply = ui.state.response
                last_assistant_reply_at = perf_counter()
            conversation_deadline = _next_conversation_deadline(settings)
            ui.set_conversation_window_remaining(settings.conversation_window_seconds)
            cooldown_until = perf_counter() + settings.wake_cooldown_seconds
        else:
            ui.set_response("Wake matched. Waiting for your request...")
            ui.set_mode(
                "conversation_window",
                f"Conversation window active: {settings.conversation_window_seconds:.1f}s remaining",
            )
            ui.log_event(
                f"Conversation window active: {settings.conversation_window_seconds:.1f}s remaining"
            )


def _transcribe_audio(
    audio_handler: AudioHandler,
    ui: TerminalUI,
    audio,
    wake_scan: bool = False,  # noqa: ANN001
) -> str:
    if wake_scan:
        ui.set_mode("wake_detected", "Potential wake speech detected. Transcribing...")
        ui.log_event("Passive wake scan captured speech.")
    else:
        ui.set_mode("transcribing", "Running MLX Whisper transcription...")
        ui.log_event("Captured audio. Starting transcription.")

    stt_start = perf_counter()
    try:
        transcript = audio_handler.transcribe_audio(audio)
    except AudioTranscriptionError as exc:
        ui.record_latency("stt", perf_counter() - stt_start)
        _handle_dependency_failure(ui, "stt_error", "Transcription failed", exc)
        return ""
    ui.record_latency("stt", perf_counter() - stt_start)
    if wake_scan and transcript:
        ui.set_transcript(transcript)
        ui.set_response("Wake scan captured speech. Matching wake phrase...")
    return transcript


def _bootstrap_connection(
    ollama_client: OllamaClient,
    ui: TerminalUI,
    text_input_mode: bool,
) -> bool:
    try:
        version = ollama_client.healthcheck()
    except OllamaClientError as exc:
        ui.show_startup_failure(f"Ollama startup check failed: {exc}")
        return False

    ui.set_connection(
        version=str(version.get("version", "unknown")),
        text_input_mode=text_input_mode,
    )
    return True


def _capture_audio(
    capture_fn: Callable[[], object | None],
    ui: TerminalUI,
) -> tuple[object | None, bool]:
    capture_start = perf_counter()
    try:
        audio = capture_fn()
    except AudioCaptureError as exc:
        ui.record_latency("capture", perf_counter() - capture_start)
        _handle_dependency_failure(ui, "capture_error", "Microphone capture failed", exc)
        return None, True

    ui.record_latency("capture", perf_counter() - capture_start)
    return audio, False


def _handle_dependency_failure(
    ui: TerminalUI,
    mode: str,
    prefix: str,
    exc: Exception,
    recoverable: bool = True,
) -> None:
    detail = str(exc).strip()
    message = f"{prefix}: {detail}" if detail else prefix
    if recoverable:
        ui.show_dependency_error(mode, message)
        return
    ui.show_startup_failure(message)


def _process_transcript_turn(
    transcript: str,
    settings: Settings,
    router: HybridRouter,
    ollama_client: OllamaClient,
    tts: MacOSTTS,
    ui: TerminalUI,
) -> None:
    turn_start = perf_counter()
    ui.reset_turn()
    ui.set_transcript(transcript)
    ui.log_event(f"Transcript ready: {transcript}")
    ui.set_mode("thinking", "Querying memory and generating a reply...")
    ui.log_event("Running memory recall and router.")
    router_start = perf_counter()
    try:
        prepared = router.prepare_turn(transcript)
    except Exception as exc:
        ui.record_latency("router", perf_counter() - router_start)
        ui.record_latency("total", perf_counter() - turn_start)
        _handle_dependency_failure(ui, "router_error", "Turn preparation failed", exc)
        return
    ui.record_latency("router", perf_counter() - router_start)
    ui.set_memory_hits(len(prepared.memory_hits))
    ui.log_event(f"Retrieved {len(prepared.memory_hits)} memory hit(s).")
    if prepared.saved_items:
        ui.add_saved_items(prepared.saved_items)

    if not prepared.fixed_reply and not prepared.final_messages:
        ui.record_latency("total", perf_counter() - turn_start)
        ui.log_event("No spoken response was generated.")
        ui.set_mode("idle", "No spoken response generated.")
        return

    chunker = PhraseChunker(settings)
    response_parts: list[str] = []
    stream_start = perf_counter()
    speech_start: float | None = None
    first_token_recorded = False
    tts.start_turn()
    ui.set_mode("streaming", "Streaming response and speaking chunks...")

    if prepared.final_messages:
        ui.log_event("Streaming final response from Ollama.")
        stream_source = ollama_client.stream_chat(prepared.final_messages)
    else:
        ui.log_event("Streaming fixed reply through chunked TTS.")
        stream_source = iter([prepared.fixed_reply])

    try:
        for piece in _stream_and_chunk(
            stream_source=stream_source,
            settings=settings,
            chunker=chunker,
            tts=tts,
            ui=ui,
            response_parts=response_parts,
        ):
            if not first_token_recorded and piece.strip():
                ui.record_latency("first_token", perf_counter() - stream_start)
                first_token_recorded = True
            if speech_start is None and ui.state.emitted_chunk_count > 0:
                speech_start = perf_counter()
    except Exception as exc:
        final_text = "".join(response_parts).strip()
        flushed_chunks = _flush_chunker_tail(chunker=chunker, tts=tts, ui=ui)
        if flushed_chunks:
            ui.log_event(
                f"Flushed {flushed_chunks} buffered speech chunk(s) after stream failure."
            )
        if final_text:
            ui.set_response(final_text)
            ui.log_event("Preserved partial response text after stream failure.")
        tts.finish_turn()
        ui.record_latency("stream_total", perf_counter() - stream_start)
        ui.record_latency("total", perf_counter() - turn_start)
        _handle_dependency_failure(ui, "stream_error", "Response streaming failed", exc)
        return

    final_text = "".join(response_parts).strip()
    if not final_text:
        ui.record_latency("stream_total", perf_counter() - stream_start)
        ui.record_latency("total", perf_counter() - turn_start)
        ui.log_event("No spoken response was generated.")
        ui.set_mode("idle", "No spoken response generated.")
        tts.finish_turn()
        return

    ui.set_response(final_text)
    ui.set_mode("speaking", "Waiting for queued speech chunks to finish...")
    tts_errors = tts.finish_turn()
    if speech_start is not None:
        ui.record_latency("tts", perf_counter() - speech_start)
    ui.record_latency("stream_total", perf_counter() - stream_start)
    ui.record_latency("total", perf_counter() - turn_start)
    if tts_errors:
        ui.log_event("Retained final text response despite TTS playback failure.")
        _handle_dependency_failure(
            ui,
            "tts_error",
            "Speech playback completed with errors",
            tts_errors[0],
        )
        return
    ui.log_event("Finished streamed playback.")
    ui.set_mode("ready", "Turn complete. Waiting for the next turn.")


def _stream_and_chunk(
    stream_source: Iterator[str],
    settings: Settings,
    chunker: PhraseChunker,
    tts: MacOSTTS,
    ui: TerminalUI,
    response_parts: list[str],
) -> Iterator[str]:
    playback_started = False
    pending_chunks: list[str] = []

    for piece in stream_source:
        response_parts.append(piece)
        ui.set_response("".join(response_parts).strip())
        for chunk in chunker.push(piece):
            if playback_started:
                _emit_chunk(tts=tts, ui=ui, chunk=chunk)
            else:
                pending_chunks.append(chunk)
        if not playback_started and _should_start_playback(
            settings=settings,
            pending_chunks=pending_chunks,
        ):
            _emit_pending_chunks(tts=tts, ui=ui, pending_chunks=pending_chunks)
            playback_started = True
        yield piece

    for chunk in chunker.finish():
        if playback_started:
            _emit_chunk(tts=tts, ui=ui, chunk=chunk)
        else:
            pending_chunks.append(chunk)
    _emit_pending_chunks(tts=tts, ui=ui, pending_chunks=pending_chunks)


def _should_start_playback(
    settings: Settings,
    pending_chunks: list[str],
) -> bool:
    if not pending_chunks:
        return False
    buffered_chars = sum(len(chunk) for chunk in pending_chunks)
    if len(pending_chunks) >= 2:
        return True
    return buffered_chars >= settings.tts_stream_start_buffer_chars


def _emit_pending_chunks(
    tts: MacOSTTS,
    ui: TerminalUI,
    pending_chunks: list[str],
) -> None:
    while pending_chunks:
        _emit_chunk(tts=tts, ui=ui, chunk=pending_chunks.pop(0))


def _emit_chunk(
    tts: MacOSTTS,
    ui: TerminalUI,
    chunk: str,
) -> None:
    tts.enqueue_chunk(chunk)
    ui.record_emitted_chunk(chunk)


def _flush_chunker_tail(
    chunker: PhraseChunker,
    tts: MacOSTTS,
    ui: TerminalUI,
) -> int:
    flushed_chunks = 0
    for chunk in chunker.finish():
        _emit_chunk(tts=tts, ui=ui, chunk=chunk)
        flushed_chunks += 1
    return flushed_chunks


def _next_conversation_deadline(settings: Settings) -> float:
    return perf_counter() + settings.conversation_window_seconds


def _window_active(now: float, deadline: float | None) -> bool:
    return deadline is not None and now < deadline


def _remaining_window(now: float, deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - now)


def _cooldown_active(now: float, cooldown_until: float) -> bool:
    return now < cooldown_until


def _should_suppress_self_audio_echo(
    transcript: str,
    last_assistant_reply: str,
    last_assistant_reply_at: float,
    recent_spoken_chunks: deque[tuple[str, float]],
    now: float,
    settings: Settings,
) -> bool:
    reply_match = False
    if last_assistant_reply and last_assistant_reply_at:
        reply_match = (
            now - last_assistant_reply_at <= settings.self_audio_guard_seconds
            and text_similarity(transcript, last_assistant_reply)
            >= settings.self_audio_similarity_threshold
        )

    chunk_threshold = max(settings.self_audio_similarity_threshold + 0.08, 0.86)
    chunk_match = any(
        now - spoken_at <= settings.self_audio_guard_seconds
        and text_similarity(transcript, chunk_text) >= chunk_threshold
        for chunk_text, spoken_at in recent_spoken_chunks
    )

    return reply_match or chunk_match


if __name__ == "__main__":
    main()
