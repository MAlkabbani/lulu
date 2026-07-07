# Architecture

Lulu VAIA is a local-first voice assistant starter for Apple Silicon Macs. The runtime is intentionally small, with clear module boundaries and a narrow dependency surface.

## Design Goals

- keep inference and memory fully local
- favor observable, operator-friendly behavior over hidden automation
- keep the backend tool surface small, validated, and auditable
- make incremental changes easy without rewriting the whole stack

## Runtime Flow

```text
Microphone
  -> AudioHandler
     -> wake preprocessing
     -> acoustic wake scoring
     -> mlx-whisper transcription when needed
  -> app_core.RuntimeController
     -> runtime state, event emission, and mode ownership
  -> HybridRouter
     -> explicit memory save or chat/tool path
  -> Ollama
     -> local chat + embeddings
  -> MemoryManager
     -> ChromaDB recall and upsert
  -> MacOSTTS
     -> streamed speech chunks via macOS say
  -> EventBus
     -> TerminalUI and local service subscribers
  -> TerminalUI
     -> live operator feedback and diagnostics
  -> backend_service
     -> local HTTP + WebSocket boundary for the current macOS shell and future local clients
```

## Module Map

- `main.py`: CLI bootstrap and adapter over the extracted runtime controller
- `config.py`: environment-backed runtime settings, app-path defaults, and wake guidance text
- `app_core/`: runtime controller, event bus, path policy, dependency probes, and reusable runtime models
- `macos_app/`: thin SwiftUI desktop shell for voice controls, diagnostics, and settings
- `audio_handler.py`: microphone capture, transcription, TTS chunking, and playback coordination
- `pdf_audiobook.py`: offline PDF validation, text cleanup, sectioning, local audiobook export, and export playback helpers
- `wake_detection.py`: acoustic wake matching, scoring, and fast-path eligibility
- `llm_router.py`: memory recall, tool registration, validation, and bounded tool orchestration
- `memory_manager.py`: ChromaDB persistence, exact-duplicate collapse, revision-preserving conflicting updates, and retrieval
- `ollama_client.py`: local Ollama transport wrapper for health checks, embeddings, and chat
- `terminal_ui.py`: rich-based dashboard for latency, wake, memory, and response visibility
- `backend_service/`: local authenticated HTTP + WebSocket service boundary for desktop and other local clients
- `scripts/pdf_to_audiobook.py`: repo-local CLI wrapper for the offline audiobook workflow

## External Dependencies

- `mlx-whisper`: speech-to-text on Apple Silicon
- `ollama`: local chat model and embeddings
- `chromadb`: persistent semantic memory store
- `pypdf`: text-based PDF parsing for offline audiobook preparation
- `ffmpeg`: optional local post-processing for portable audiobook copies
- `sounddevice`: microphone input
- `rich`: terminal observability layer
- `fastapi` + `uvicorn`: local backend service boundary for the current macOS desktop shell and future local clients
- macOS `say`: built-in text-to-speech

## Quality Standards

- keep settings explicit and environment-driven
- validate tool arguments before backend execution
- validate file paths and reject unsupported PDF states at the boundary
- keep local helper startup authenticated and ownership-aware before issuing privileged requests
- bound long-lived queues and worker pools so local clients cannot exhaust the helper
- preserve local-only operation and avoid cloud fallbacks
- prefer focused regression tests over broad fragile suites
- document behavior changes in `README.md` or `docs/` when runtime behavior changes

## Current Hard Constraints

- Apple Silicon and macOS are the supported platform target
- assistant playback is non-interruptible today
- the published tool surface is memory-only by design
- wake behavior combines acoustic and transcript evidence, not transcript matching alone
- PDF audiobook generation supports text-based PDFs first; OCR is explicitly deferred
