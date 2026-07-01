from __future__ import annotations

import argparse
from collections.abc import Iterator
from time import perf_counter

from audio_handler import AudioHandler, MacOSTTS, PhraseChunker
from config import Settings
from llm_router import HybridRouter
from memory_manager import MemoryManager
from ollama_client import OllamaClient
from terminal_ui import TerminalUI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lulu VAIA local voice assistant")
    parser.add_argument(
        "--text-input",
        action="store_true",
        help="Use typed input instead of microphone capture for quick testing.",
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
    tts.set_on_chunk_spoken(ui.record_spoken_chunk)

    ui.start()
    version = ollama_client.healthcheck()
    ui.set_connection(
        version=str(version.get("version", "unknown")),
        text_input_mode=args.text_input,
    )

    try:
        while True:
            turn_start = perf_counter()
            ui.reset_turn()
            ui.set_mode("idle", "Waiting for the next turn.")

            if args.text_input:
                transcript = ui.prompt_text()
                ui.record_latency("capture", 0.0)
                ui.log_event("Accepted text input.")
            else:
                ui.set_mode("listening", "Listening for speech...")
                ui.log_event("Listening for speech.")
                capture_start = perf_counter()
                audio = audio_handler.record_until_silence()
                capture_elapsed = perf_counter() - capture_start
                ui.record_latency("capture", capture_elapsed)
                if audio is None:
                    ui.log_event("No speech detected during capture.")
                    ui.set_mode("idle", "No speech detected. Waiting again.")
                    continue

                ui.set_mode("transcribing", "Running MLX Whisper transcription...")
                ui.log_event("Captured audio. Starting transcription.")
                stt_start = perf_counter()
                transcript = audio_handler.transcribe_audio(audio)
                ui.record_latency("stt", perf_counter() - stt_start)

            if not transcript:
                ui.log_event("Transcript was empty.")
                ui.set_mode("idle", "No transcript captured. Waiting again.")
                continue

            ui.set_transcript(transcript)
            ui.log_event(f"Transcript ready: {transcript}")
            ui.set_mode("thinking", "Querying memory and generating a reply...")
            ui.log_event("Running memory recall and router.")
            router_start = perf_counter()
            prepared = router.prepare_turn(transcript)
            ui.record_latency("router", perf_counter() - router_start)
            ui.set_memory_hits(len(prepared.memory_hits))
            ui.log_event(f"Retrieved {len(prepared.memory_hits)} memory hit(s).")
            if prepared.saved_items:
                ui.add_saved_items(prepared.saved_items)

            if not prepared.fixed_reply and not prepared.final_messages:
                ui.record_latency("total", perf_counter() - turn_start)
                ui.log_event("No spoken response was generated.")
                ui.set_mode("idle", "No spoken response generated.")
                continue

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

            for piece in _stream_and_chunk(
                stream_source=stream_source,
                chunker=chunker,
                tts=tts,
                ui=ui,
                response_parts=response_parts,
            ):
                if not first_token_recorded and piece.strip():
                    ui.record_latency("first_token", perf_counter() - stream_start)
                    first_token_recorded = True
                if speech_start is None and response_parts:
                    speech_start = perf_counter()

            final_text = "".join(response_parts).strip()
            if not final_text:
                ui.record_latency("stream_total", perf_counter() - stream_start)
                ui.record_latency("total", perf_counter() - turn_start)
                ui.log_event("No spoken response was generated.")
                ui.set_mode("idle", "No spoken response generated.")
                tts.finish_turn()
                continue

            ui.set_response(final_text)
            ui.set_mode("speaking", "Waiting for queued speech chunks to finish...")
            tts.finish_turn()
            if speech_start is not None:
                ui.record_latency("tts", perf_counter() - speech_start)
            ui.record_latency("stream_total", perf_counter() - stream_start)
            ui.record_latency("total", perf_counter() - turn_start)
            ui.log_event("Finished streamed playback.")
            ui.set_mode("ready", "Turn complete. Waiting for the next turn.")
    except KeyboardInterrupt:
        tts.close()
        ui.stop()
        print("\nGoodbye.")


def _stream_and_chunk(
    stream_source: Iterator[str],
    chunker: PhraseChunker,
    tts: MacOSTTS,
    ui: TerminalUI,
    response_parts: list[str],
) -> Iterator[str]:
    for piece in stream_source:
        response_parts.append(piece)
        ui.set_response("".join(response_parts).strip())
        for chunk in chunker.push(piece):
            tts.enqueue_chunk(chunk)
            ui.record_emitted_chunk(chunk)
        yield piece

    for chunk in chunker.finish():
        tts.enqueue_chunk(chunk)
        ui.record_emitted_chunk(chunk)


if __name__ == "__main__":
    main()
