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
  -> HybridRouter
     -> explicit memory save or chat/tool path
  -> Ollama
     -> local chat + embeddings
  -> MemoryManager
     -> ChromaDB recall and upsert
  -> MacOSTTS
     -> streamed speech chunks via macOS say
  -> TerminalUI
     -> live operator feedback and diagnostics
```

## Module Map

- `main.py`: app bootstrap, mode selection, turn loop orchestration, and degraded-mode handling
- `config.py`: environment-backed runtime settings and wake guidance text
- `audio_handler.py`: microphone capture, transcription, TTS chunking, and playback coordination
- `pdf_audiobook.py`: offline PDF validation, text cleanup, sectioning, local audiobook export, and export playback helpers
- `wake_detection.py`: acoustic wake matching, scoring, and fast-path eligibility
- `llm_router.py`: memory recall, tool registration, validation, and bounded tool orchestration
- `memory_manager.py`: ChromaDB persistence, deduplication, and retrieval
- `ollama_client.py`: local Ollama transport wrapper for health checks, embeddings, and chat
- `terminal_ui.py`: rich-based dashboard for latency, wake, memory, and response visibility
- `scripts/pdf_to_audiobook.py`: repo-local CLI wrapper for the offline audiobook workflow

## External Dependencies

- `mlx-whisper`: speech-to-text on Apple Silicon
- `ollama`: local chat model and embeddings
- `chromadb`: persistent semantic memory store
- `pypdf`: text-based PDF parsing for offline audiobook preparation
- `ffmpeg`: optional local post-processing for portable audiobook copies
- `sounddevice`: microphone input
- `rich`: terminal observability layer
- macOS `say`: built-in text-to-speech

## Quality Standards

- keep settings explicit and environment-driven
- validate tool arguments before backend execution
- validate file paths and reject unsupported PDF states at the boundary
- preserve local-only operation and avoid cloud fallbacks
- prefer focused regression tests over broad fragile suites
- document behavior changes in `README.md` or `docs/` when runtime behavior changes

## Current Hard Constraints

- Apple Silicon and macOS are the supported platform target
- assistant playback is non-interruptible today
- the published tool surface is memory-only by design
- wake behavior combines acoustic and transcript evidence, not transcript matching alone
- PDF audiobook generation supports text-based PDFs first; OCR is explicitly deferred
