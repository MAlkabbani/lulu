# Lulu VAIA

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Support: Buy Me a Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/webeworx)
[![Platform: macOS Apple Silicon](https://img.shields.io/badge/Platform-macOS%20Apple%20Silicon-111111?logo=apple&logoColor=white)](./docs/operations.md)

```text
 _      _   _      _   _
| |    | | | |    | | | |
| |    | | | |    | | | |
| |___ | |_| |___ | |_| |
|_____|\___/|_____|\___/

Lulu
Local Voice AI Assistant Starter
Flexible. Local-first. Extensible.
```

Lulu VAIA is a fully local, Apple Silicon-first voice assistant starter for macOS. It combines `mlx-whisper` for speech-to-text, Ollama for chat and embeddings, ChromaDB for long-term memory, and the native macOS `say` command for zero-setup speech output.

## Why This Repo Exists

- give you a strong local-first starter for voice AI experiments
- keep the architecture small enough to understand and extend
- expose wake, memory, latency, and tool behavior in a visible terminal dashboard
- provide a production-minded baseline instead of a throwaway prototype

## Support

Support ongoing development: [Buy Me a Coffee](https://buymeacoffee.com/webeworx)

## Documentation

- [Documentation Index](./docs/README.md)
- [Architecture](./docs/architecture.md)
- [Operations Runbook](./docs/operations.md)
- [Uninstall Guide](./docs/operations.md#uninstall)
- [PDF To Audiobooks](./docs/pdf-audiobooks.md)
- [Product Requirements](./docs/prd.md)
- [Decision Log](./docs/decision-log.md)
- [Wake Performance Report](./docs/wake-performance-report.md)
- [Roadmap](./ROADMAP.md)
- [Contributing Guide](./CONTRIBUTING.md)
- [Security Policy](./SECURITY.md)
- [Support Guide](./SUPPORT.md)
- [Licensing Options](./docs/licensing-options.md)

## Current Baseline

- fully local runtime on macOS Apple Silicon
- hybrid acoustic-plus-transcript wake detection
- bounded memory tool orchestration through Ollama native tool calls
- ChromaDB-backed canonical memory with deduplication and metadata
- streamed, clause-aware TTS chunking on top of macOS `say`
- offline PDF-to-audiobook export for text-based PDFs, with optional local portable conversion
- rich terminal observability for mode, wake, latency, memory, and action flow

## Quick Start

Use the tracked automation scripts from the repo root:

```bash
chmod +x scripts/install_lulu.sh scripts/start_lulu.sh
./scripts/install_lulu.sh
./scripts/start_lulu.sh
```

Helpful variants:

```bash
./scripts/install_lulu.sh --dry-run
./scripts/start_lulu.sh --check
./scripts/start_lulu.sh --mode text
./scripts/start_lulu.sh --mode turn-based
```

Offline PDF-to-audiobook preview:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf --dry-run
```

## Manual Setup

Install machine dependencies:

```bash
brew update
brew install python@3.12 portaudio ffmpeg ollama
```

Create a virtual environment and install runtime dependencies:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

Start Ollama and pull the default models:

```bash
ollama serve
ollama pull llama3.2:3b
ollama pull nomic-embed-text
```

## Development Setup

If you want to run tests or lint checks, install the development dependencies instead:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements-dev.txt
```

## How Lulu Works

### Runtime Modes

- `voice`: always-on passive listening with wake detection
- `text`: typed-input mode for fast router and memory iteration
- `turn-based`: one-turn microphone mode for troubleshooting

### Memory Paths

Lulu supports two memory flows:

1. Explicit save:

```text
insert info my dog's name is Nori
```

This bypasses the chat model and writes a canonical memory entry directly.

2. Chat plus tools:

Normal user turns can recall memories, inspect recent entries, explain a prior memory hit, or save a durable fact through the validated backend tool registry.

### Offline PDF Audiobooks

Lulu also ships a repo-local offline audiobook utility for text-based PDFs:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks \
  --portable-format m4a
```

This workflow:

- validates local PDF paths and rejects encrypted or corrupted files safely
- cleans common PDF artifacts such as repeated headers, footers, page numbers, and broken wraps
- detects chapter boundaries conservatively, including short title-like subtitles after chapter headings
- writes cleaned text plus local AIFF section files with native macOS `say`
- can optionally post-process those AIFF files into `wav`, `m4a`, or `mp3` with local `ffmpeg`
- stops on scanned or image-only PDFs and reports that OCR is currently deferred

## How To Use PDF Audiobooks

End-user flow from the repo root:

1. Install the repo dependencies and activate `.venv`.
2. Run a dry-run first to confirm Lulu can read and clean the PDF text.
3. Generate AIFF chapter files once the preview looks right.
4. Add `--portable-format` only if you also want `wav`, `m4a`, or `mp3` copies.
5. Open the generated book folder under `outputs/audiobooks/` and use `manifest.json` as the run summary.

Recommended first run:

```bash
source .venv/bin/activate
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf --dry-run
```

If the preview looks good, generate audio:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks
```

If you also want a more portable local format:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks \
  --portable-format m4a
```

What the end-user gets:

- `text/full_text.txt`: cleaned full-book text
- `text/*.txt`: per-section text files after chapter splitting
- `audio/*.aiff`: default local audio exports from macOS `say`
- `audio/*.<format>`: optional portable copies when `--portable-format` is used
- `manifest.json`: metadata, extraction summary, output file list, and limitations

When to expect a refusal instead of output:

- the PDF is encrypted
- the PDF is corrupted or unsupported
- the PDF is scanned or image-only and yields no extractable text
- `--portable-format` was requested but `ffmpeg` is not installed

### Wake Flow

The wake pipeline uses a hybrid strategy:

1. short wake-scan audio capture
2. preprocessing plus feature extraction
3. acoustic wake scoring with DTW
4. fast-path acceptance for short, confident wakes when possible
5. `mlx-whisper` transcription when transcript confirmation is needed
6. self-audio suppression and cooldown to avoid echo retriggers

## Architecture Snapshot

```text
Microphone
  -> AudioHandler
     -> wake preprocessing + acoustic scoring
     -> mlx-whisper transcription when needed
  -> HybridRouter
     -> explicit save path or chat/tool path
  -> MemoryManager
     -> ChromaDB recall and upsert
  -> MacOSTTS
     -> streamed speech chunks
  -> TerminalUI
     -> live observability
```

## Repository Structure

```text
.
├── .env.example
├── .github/
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── README.md
├── ROADMAP.md
├── SECURITY.md
├── SUPPORT.md
├── audio_handler.py
├── config.py
├── docs/
│   ├── README.md
│   ├── architecture.md
│   ├── decision-log.md
│   ├── licensing-options.md
│   ├── operations.md
│   ├── prd.md
│   └── wake-performance-report.md
├── llm_router.py
├── main.py
├── memory_manager.py
├── ollama_client.py
├── pdf_audiobook.py
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── scripts/
│   └── pdf_to_audiobook.py
├── terminal_ui.py
├── tests/
├── wake_benchmark.py
└── wake_detection.py
```

## Configuration

Start from the tracked example:

```bash
cp .env.example .env
```

Key settings include:

- `OLLAMA_BASE_URL`
- `OLLAMA_CHAT_MODEL`
- `OLLAMA_EMBED_MODEL`
- `MLX_WHISPER_MODEL`
- `WAKE_PHRASE`
- `PRACTICAL_VOICE_MODE`
- `CHROMA_PATH`

The Python settings layer now validates numeric and boolean environment values with clearer errors, and the shell scripts parse `.env` files as plain key/value config instead of executing them as shell code.

## Quality And Safety

- validated tool registry with bounded tool loops
- memory treated as untrusted context, not executable instruction text
- non-shell TTS invocation through `subprocess.run([...])`
- explicit degraded-mode visibility in the terminal UI
- focused regression tests for routing, wake handling, memory, TTS, and runtime resilience

## Validation

Run the main test suite:

```bash
python -m pytest -q
```

Useful extra checks:

```bash
python -m compileall .
ruff check .
```

## Open-Source Workflow

- use [CONTRIBUTING.md](./CONTRIBUTING.md) before opening a pull request
- use the GitHub issue templates for bugs and feature requests
- keep roadmap discussions aligned with [ROADMAP.md](./ROADMAP.md)
- report security concerns privately as described in [SECURITY.md](./SECURITY.md)

## License

This repository now uses the [MIT License](./LICENSE). It is the most common low-friction choice for starter repositories like this one because it makes reuse and extension easy for individual developers and teams.
