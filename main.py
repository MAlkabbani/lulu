from __future__ import annotations

import argparse

from app_core.runtime_controller import (
    RuntimeController,
)
from app_core.runtime_controller import (
    bootstrap_connection as _bootstrap_connection,
)
from app_core.runtime_controller import (
    capture_audio as _capture_audio,
)
from app_core.runtime_controller import (
    cooldown_active as _cooldown_active,
)
from app_core.runtime_controller import (
    process_transcript_turn as _process_transcript_turn,
)
from app_core.runtime_controller import (
    remaining_window as _remaining_window,
)
from app_core.runtime_controller import (
    should_suppress_self_audio_echo as _should_suppress_self_audio_echo,
)
from app_core.runtime_controller import (
    stream_and_chunk as _stream_and_chunk,
)
from app_core.runtime_controller import (
    transcribe_audio as _transcribe_audio,
)
from app_core.runtime_controller import (
    wake_rejection_guidance as _wake_rejection_guidance,
)
from app_core.runtime_controller import (
    wake_rejection_response as _wake_rejection_response,
)
from app_core.runtime_controller import (
    window_active as _window_active,
)
from audio_handler import AudioHandler, MacOSTTS
from config import Settings
from llm_router import HybridRouter
from memory_manager import MemoryManager
from ollama_client import OllamaClient
from terminal_ui import TerminalUI

# Preserve test-facing compatibility shims for helper-level imports from `main`.
_EXPORTED_HELPERS = (
    _bootstrap_connection,
    _capture_audio,
    _cooldown_active,
    _process_transcript_turn,
    _remaining_window,
    _should_suppress_self_audio_echo,
    _stream_and_chunk,
    _transcribe_audio,
    _wake_rejection_guidance,
    _wake_rejection_response,
    _window_active,
)


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
