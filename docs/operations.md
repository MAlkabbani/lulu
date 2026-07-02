# Installation And Operations

This runbook documents the production-style install and startup workflows for Lulu VAIA on macOS Apple Silicon.

## Scope

- Fresh-system bootstrap on macOS using Homebrew
- Repo-local Python environment setup in `.venv`
- Ollama readiness and required model validation
- Startup, runtime checks, monitoring, and graceful shutdown
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

From the repo root:

```bash
chmod +x scripts/install_lulu.sh scripts/start_lulu.sh
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

### Speech output sounds choppy

The runtime now avoids emitting very short punctuation-led chunks too early, but phrase-boundary streaming can still sound less smooth than sentence-sized playback. If this remains noticeable in your environment, use turn-based mode to isolate whether the issue is specific to continuous voice flow:

```bash
./scripts/start_lulu.sh --mode turn-based
```
