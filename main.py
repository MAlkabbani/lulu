from __future__ import annotations

import argparse
from time import perf_counter

from audio_handler import AudioHandler, MacOSTTS
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
            result = router.handle_transcript(transcript)
            ui.record_latency("router", perf_counter() - router_start)
            ui.set_memory_hits(len(result.memory_hits))
            ui.log_event(f"Retrieved {len(result.memory_hits)} memory hit(s).")
            if result.saved_items:
                ui.add_saved_items(result.saved_items)

            if not result.reply_text:
                ui.record_latency("total", perf_counter() - turn_start)
                ui.log_event("No spoken response was generated.")
                ui.set_mode("idle", "No spoken response generated.")
                continue

            ui.set_response(result.reply_text)
            ui.log_event(f"Response ready: {result.reply_text}")
            ui.set_mode("speaking", "Speaking response...")
            tts_start = perf_counter()
            tts.speak(result.reply_text)
            ui.record_latency("tts", perf_counter() - tts_start)
            ui.record_latency("total", perf_counter() - turn_start)
            ui.log_event("Finished speaking response.")
            ui.set_mode("ready", "Turn complete. Waiting for the next turn.")
    except KeyboardInterrupt:
        ui.stop()
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
