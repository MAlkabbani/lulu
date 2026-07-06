from __future__ import annotations

import argparse

from app_core.runtime_controller import (
    RuntimeController,
    bootstrap_connection as _bootstrap_connection,
    capture_audio as _capture_audio,
    cooldown_active as _cooldown_active,
    next_conversation_deadline as _next_conversation_deadline,
    process_transcript_turn as _process_transcript_turn,
    recent_assistant_audio_guard_active as _recent_assistant_audio_guard_active,
    remaining_window as _remaining_window,
    should_start_playback as _should_start_playback,
    should_suppress_self_audio_echo as _should_suppress_self_audio_echo,
    stream_and_chunk as _stream_and_chunk,
    transcribe_audio as _transcribe_audio,
    wake_rejection_guidance as _wake_rejection_guidance,
    wake_rejection_response as _wake_rejection_response,
    window_active as _window_active,
)
from audio_handler import AudioHandler, MacOSTTS
from config import Settings
from llm_router import HybridRouter
from memory_manager import MemoryManager
from ollama_client import OllamaClient
from terminal_ui import TerminalUI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lulu VAIA local voice assistant")
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
    controller = RuntimeController(
        settings,
        ollama_client=ollama_client,
        memory_manager=memory_manager,
        router=router,
        audio_handler=audio_handler,
        tts=tts,
        ui=ui,
    )
    controller.run(
        turn_based=args.turn_based,
        bootstrap_fn=_bootstrap_connection,
    )


if __name__ == "__main__":
    main()
