# Installation And Operations

This runbook documents the production-style install and startup workflows for Lulu VAIA on macOS Apple Silicon.

## Scope

- Fresh-system bootstrap on macOS using Homebrew
- Repo-local Python environment setup in `.venv`
- Ollama readiness and required model validation
- Startup, runtime checks, monitoring, and graceful shutdown
- Offline PDF-to-audiobook generation for text-based PDFs
- Safe rollback boundaries for failed installation attempts

## Prerequisites

Before running the automation, make sure the target machine has:

- macOS on Apple Silicon
- internet access for Homebrew and Ollama model downloads
- microphone hardware available to the user session
- Homebrew installed and usable by the current shell
- a checked-out Lulu repository

## Files Added For Operations

- `scripts/install_lulu.sh`: fresh-system installer
- `scripts/start_lulu.sh`: runtime startup and supervision wrapper
- `.env.example`: tracked configuration template
- `.env`: local operator overrides created from `.env.example` on first install

## Fresh Installation

### Default install

Clone the repository and enter the checkout first:

```bash
git clone https://github.com/MAlkabbani/lulu.git
cd lulu
```

From that checkout root:

```bash
./scripts/install_lulu.sh
```

The installer performs these steps in order:

1. validates that it is running on macOS and that Homebrew plus curl exist
2. installs or verifies `python@3.12`, `portaudio`, `ffmpeg`, and `ollama`
3. creates `.env` from `.env.example` if it does not exist yet
4. creates `.venv` and installs Python dependencies from `requirements.txt`
5. starts a temporary managed Ollama service if Ollama is offline
6. validates or pulls `llama3.2:3b` and `nomic-embed-text`
7. performs a focused Python import verification pass

### Dry-run install validation

Use dry-run mode to validate the sequence without modifying the system:

```bash
./scripts/install_lulu.sh --dry-run
```

### Rollback behavior

If installation fails, the installer rolls back repo-local changes it created during the current run, including:

- a newly created `.venv`
- a newly created `.env`
- Ollama models pulled by this installer run
- a temporary Ollama process started by this installer run

By default, the installer does **not** uninstall Homebrew formulas on rollback because they are system-wide and may be shared with other projects.

If you explicitly want formula rollback for packages installed during the same run:

```bash
ALLOW_SYSTEM_PACKAGE_ROLLBACK=1 ./scripts/install_lulu.sh
```

Use that mode carefully.

## Runtime Startup

### Default voice mode

```bash
./scripts/start_lulu.sh
```

This wrapper:

1. loads `.env` if present
2. verifies `.venv` exists
3. checks that Ollama is online and starts a managed `ollama serve` if needed
4. verifies the required Ollama models are already present
5. launches Lulu in the foreground
6. monitors the process and optionally restarts it on unexpected exit
7. forwards shutdown signals and stops managed child processes cleanly

### Alternate modes

Text mode:

```bash
./scripts/start_lulu.sh --mode text
```

Turn-based troubleshooting:

```bash
./scripts/start_lulu.sh --mode turn-based
```

### Check-only mode

Validate startup prerequisites without launching Lulu:

```bash
./scripts/start_lulu.sh --check
```

### Restart policy

The startup wrapper supports environment-driven monitoring:

```bash
export LULU_RESTART_ON_FAILURE="true"
export LULU_MAX_RESTARTS="2"
export LULU_RESTART_BACKOFF_SECONDS="2"
./scripts/start_lulu.sh
```

## Offline PDF Audiobooks

### Scope

This repo also includes a separate offline utility for turning text-based PDFs into cleaned text plus local AIFF section files. It does not run through `main.py` and does not affect wake, router, or interactive voice behavior.

### Run the workflow

From the repo root:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf --dry-run
```

Generate audio files locally:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks
```

Generate and immediately play the export:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks \
  --play-after-export
```

Generate portable copies with a second local conversion pass:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks \
  --portable-format wav
```

### Supported inputs

- local `.pdf` files only
- text-based PDFs first
- macOS-local export through native `say`
- playback of generated exports through native macOS `afplay` or `say`

### Current limitations

- encrypted PDFs are rejected
- corrupted or unsupported PDFs are rejected with operator-facing errors
- scanned or image-only PDFs stop early and report that OCR is currently deferred
- output is section-level AIFF files by default, with optional `wav`, `m4a`, or `mp3` copies when `--portable-format` is requested
- reruns with the same title create a new unique output folder instead of failing
- packaged M4B audiobooks are still out of scope

### Playback

Play a previously generated export directory:

```bash
python3 scripts/pdf_to_audiobook.py \
  --play-export ./outputs/audiobooks/my-local-book
```

Force text-to-speech playback from the exported text files:

```bash
python3 scripts/pdf_to_audiobook.py \
  --play-export ./outputs/audiobooks/my-local-book \
  --play-mode text
```

Operational notes:

- `--dry-run` writes text artifacts and `manifest.json`, but no media files
- `manifest.json` now records workflow state for text export, audio render, and portable conversion
- if text exists but media does not, inspect the manifest before assuming generation succeeded

### Pronunciation overrides

Simple replacement-based overrides can be provided with a JSON file:

```json
{
  "MLX": "M L X",
  "ChromaDB": "Chroma D B"
}
```

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --pronunciation-file ./pronunciations.json
```

### Ollama management

By default, the wrapper auto-starts Ollama if it is offline:

```bash
export LULU_AUTO_START_OLLAMA="true"
./scripts/start_lulu.sh
```

To require Ollama to already be up:

```bash
export LULU_AUTO_START_OLLAMA="false"
./scripts/start_lulu.sh --check
```

## Uninstall

Choose the level of removal that matches what you want to keep on the machine.

### Remove the repo checkout only

If you want to remove Lulu source code and its repo-local virtual environment together, delete the repository directory from its parent folder:

```bash
cd ..
rm -rf lulu
```

This removes the checked-out source tree, the local `.venv`, tracked docs, and repo-local generated files that live inside the checkout.

### Remove local runtime data but keep the repo

If you want to keep the source code but clear local runtime state, generated artifacts, and caches from the current checkout:

```bash
rm -rf .venv
rm -rf vault_db
rm -rf logs run
rm -rf outputs
rm -rf .pytest_cache .ruff_cache .mypy_cache
find . -type d -name __pycache__ -prune -exec rm -rf {} +
rm -f .env .env.*
```

This keeps the tracked source files while removing the local Python environment, persisted memory store, operational logs, runtime PID files, audiobook outputs, caches, and local environment overrides.

### Remove Lulu and its machine-wide dependencies

Only use this path if the machine-wide tools below were installed specifically for Lulu and are not needed by other projects.

1. Remove the Ollama models used by Lulu:

```bash
ollama rm llama3.2:3b
ollama rm nomic-embed-text
```

2. If you installed shared system dependencies for Lulu through Homebrew and no longer need them elsewhere:

```bash
brew uninstall ollama
brew uninstall ffmpeg portaudio
brew uninstall python@3.12
```

3. Remove local Ollama data if you want a full model and app cleanup on this Mac:

```bash
rm -rf ~/.ollama
rm -rf ~/Library/Application\ Support/Ollama
rm -rf ~/Library/Saved\ Application\ State/com.electron.ollama.savedState
rm -rf ~/Library/Caches/com.electron.ollama
rm -rf ~/Library/Caches/ollama
rm -rf ~/Library/WebKit/com.electron.ollama
```

### Revoke microphone access

If you want to remove microphone access after uninstalling Lulu, revoke it for the terminal or IDE host app that ran Lulu in:

- `System Settings > Privacy & Security > Microphone`

### Verify removal

Check whether the repo-local memory store still exists:

```bash
test -d vault_db && echo "vault_db still present" || echo "vault_db removed"
```

Check whether the local operational directories still exist:

```bash
test -d logs && echo "logs still present" || echo "logs removed"
test -d run && echo "run still present" || echo "run removed"
```

Check whether Ollama is still installed on the machine:

```bash
command -v ollama || echo "ollama not found"
```

## Configuration Requirements

Copying `.env.example` to `.env` gives you the recommended baseline. The most important operator-facing settings are:

- `OLLAMA_BASE_URL`
- `OLLAMA_CHAT_MODEL`
- `OLLAMA_EMBED_MODEL`
- `MLX_WHISPER_MODEL`
- `MLX_WHISPER_LANGUAGE`
- `AUDIO_INPUT_DEVICE`
- `WAKE_PHRASE`
- `CONTINUOUS_LISTENING_ENABLED`
- `LULU_AUTO_START_OLLAMA`
- `LULU_RESTART_ON_FAILURE`
- `LULU_MAX_RESTARTS`
- `LULU_RESTART_BACKOFF_SECONDS`

## Logging And Runtime Artifacts

The scripts write operational artifacts locally:

- `logs/install-*.log`
- `logs/startup-*.log`
- `logs/ollama-runtime.log`
- `run/lulu.pid`
- `run/ollama.pid`

These are local operational artifacts and should not be committed.

## Troubleshooting

### Installer says Homebrew is missing

Install Homebrew first from [brew.sh](https://brew.sh/) and rerun the installer.

### Startup wrapper says `.venv` is missing

Run:

```bash
./scripts/install_lulu.sh
```

### PDF workflow reports an encrypted file

Provide a decrypted local copy first. The repo-default workflow does not try to unlock PDFs.

### PDF workflow reports no extractable text

The file is likely scanned or image-only. OCR is not bundled into the current repo workflow.

### PDF workflow reports an existing output directory

Choose a different `--output-dir` or change `--title` so the book slug is unique.

### PDF workflow reports missing `ffmpeg`

The optional `--portable-format` conversion step depends on `ffmpeg`. Install it or rerun without portable conversion.

### Startup wrapper says a required Ollama model is missing

The installer is the supported bootstrap path:

```bash
./scripts/install_lulu.sh
```

### Lulu hears the wrong microphone

Set the macOS input device explicitly in `.env`:

```bash
AUDIO_INPUT_DEVICE="MacBook Air Microphone"
```

Then restart Lulu with `./scripts/start_lulu.sh`.

### Wake recognition is unreliable

Keep the default:

```bash
MLX_WHISPER_MODEL="mlx-community/whisper-base-mlx"
MLX_WHISPER_LANGUAGE="en"
```

The reliable continuous-listening flow is:

1. say `hey lulu`
2. wait for the conversation window
3. speak the request

If live wake capture still feels fragile in your room, enable `PRACTICAL_VOICE_MODE="true"` in `.env` and restart Lulu. That preset makes the wake scan slightly more forgiving and gives you a longer follow-up window after a successful wake.

Before changing more knobs, use the wake debug panel to check the session success rate, acoustic confidence, DTW score, SNR, average wake score, top rejection reasons, and current guidance. Those signals usually tell you whether the issue is short captures, poor signal quality, low-confidence phrase matching, or self-audio suppression.

The current tuned wake defaults for the enhanced matcher are:

```bash
WAKE_MATCH_SCORE_THRESHOLD="0.84"
WAKE_CONFIDENCE_THRESHOLD="0.73"
WAKE_ACOUSTIC_CANDIDATE_THRESHOLD="0.54"
WAKE_FAST_PATH_THRESHOLD="0.89"
WAKE_NOISE_TOLERANCE="0.70"
WAKE_MISPRONUNCIATION_TOLERANCE="0.78"
WAKE_NOISE_REDUCTION_STRENGTH="0.64"
WAKE_ECHO_SUPPRESSION_STRENGTH="0.24"
```

Tune `WAKE_NOISE_TOLERANCE` upward when the room is noisy but you still want aggressive wake acceptance, and tune `WAKE_MISPRONUNCIATION_TOLERANCE` upward when you want the acoustic matcher to offset small pronunciation drift more aggressively.

### Speech output sounds choppy

The runtime now uses grouped smoothness-first playback with delayed first speech, clause-aware chunk breaks, and short-tail merging. If seams are still noticeable in your environment, use turn-based mode to isolate whether the issue is specific to continuous voice flow:

```bash
./scripts/start_lulu.sh --mode turn-based
```
