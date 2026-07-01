from __future__ import annotations

import argparse

from audio_handler import AudioHandler, MacOSTTS
from config import Settings
from llm_router import HybridRouter
from memory_manager import MemoryManager
from ollama_client import OllamaClient


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

    version = ollama_client.healthcheck()
    print(f"Connected to Ollama {version.get('version', 'unknown')}")
    print(f"{settings.app_name} is ready. Press Ctrl+C to stop.")

    try:
        while True:
            if args.text_input:
                transcript = input("\nYou> ").strip()
            else:
                transcript = audio_handler.listen_and_transcribe()

            if not transcript:
                continue

            print(f"\nUser said: {transcript}")
            result = router.handle_transcript(transcript)
            if not result.reply_text:
                continue

            print(f"{settings.app_name}> {result.reply_text}")
            tts.speak(result.reply_text)
    except KeyboardInterrupt:
        print("\nGoodbye.")


if __name__ == "__main__":
    main()
