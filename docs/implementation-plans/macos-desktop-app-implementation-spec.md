# macOS Desktop App Implementation Spec

## Status

- Future implementation candidate
- Not part of the current shipped scope
- Intended to guide an incremental migration from the current terminal-first product to a packaged macOS desktop app

## Summary

This document turns the repository-specific desktop-app research report into a concrete implementation spec for Lulu VAIA.

The recommended path is:

- keep the current Python-first backend and module ownership boundaries
- extract a reusable runtime service core from the current CLI orchestration
- expose that backend over a stable local IPC boundary
- build a native SwiftUI macOS shell on top of that backend
- package the combined app as a signed, notarized, Apple-Silicon-only `.app`

This is intentionally not a greenfield redesign.

The spec assumes the current repository remains the source of truth for runtime behavior, especially:

- local-only execution
- Apple Silicon support
- Python-first backend ownership
- hybrid acoustic-plus-transcript wake handling
- non-interruptible playback
- small memory-focused tool surface
- separate offline PDF audiobook workflow
- strong operator/developer observability

## Why This Exists

The current repository is already modular enough to support a safe desktop migration:

- `main.py` owns runtime orchestration and mode control
- `audio_handler.py` owns capture, transcription coordination, and speech playback
- `llm_router.py` owns recall, explicit-save handling, and bounded tool orchestration
- `memory_manager.py` owns Chroma persistence and memory semantics
- `ollama_client.py` owns the local Ollama transport
- `terminal_ui.py` owns observability
- `pdf_audiobook.py` owns the separate offline audiobook workflow

That boundary should be wrapped and surfaced, not rewritten in a different stack.

## Current-State Findings That Shape The Plan

### Existing Strengths

- the runtime modules already have strong responsibility boundaries
- the project is already local-first and macOS-specific
- the current terminal UI already exposes the most important operational signals
- startup and installation scripts already encode dependency assumptions for Python, PortAudio, Ollama, and ffmpeg
- the test suite already covers the riskiest runtime flows:
  - wake handling
  - streamed TTS behavior
  - resilience and degraded states
  - memory semantics and tool routing
  - PDF workflow constraints

### Existing Friction To Remove

- startup assumes a repo checkout, repo-local `.env`, and repo-local `.venv`
- persistence paths are repo-local rather than app-owned
- microphone permission flow is currently tied to terminal execution rather than an app bundle
- observability is strong, but only through the terminal dashboard
- there is no stable GUI-facing service contract yet

### Product Boundary To Preserve

The current `docs/prd.md` explicitly treats a rich GUI as out of scope for the currently shipped product. This spec is therefore a future-state implementation plan, not a statement that the current release already includes desktop GUI support.

## Locked Constraints And Invariants

These should be treated as implementation requirements unless explicitly superseded by a later decision doc:

- platform target remains macOS on Apple Silicon
- core workflow remains local-first; no cloud dependency is introduced into the primary assistant flow
- backend remains Python-first
- current backend logic is wrapped, not casually reimplemented in Swift, JavaScript, Rust, or Flutter
- assistant playback remains non-interruptible in the initial desktop release
- wake detection continues to use the current hybrid acoustic-plus-transcript pipeline
- Chroma remains the local memory store
- Ollama remains the local LLM and embedding runtime
- OCR for scanned PDFs remains deferred
- the PDF audiobook workflow remains operationally separate from the live assistant runtime
- observability must be preserved or improved, not hidden behind a simplified chat UI

## Recommended Target Architecture

### Primary Path

- native `SwiftUI` macOS shell
- bundled Python backend helper process
- local HTTP control plane on `127.0.0.1`
- WebSocket event stream for live runtime state
- external Ollama runtime as a managed prerequisite for v1

### Fallback Path

- `Tauri` shell with the exact same Python backend helper and IPC contract

The fallback exists only if SwiftUI delivery stalls or native macOS staffing becomes the primary blocker. The backend wrapping plan does not change.

## Proposed Repository Shape

This is the target repo shape after the first desktop-foundation slices, not an immediate rewrite:

```text
.
├── app_core/
│   ├── __init__.py
│   ├── runtime_controller.py
│   ├── runtime_models.py
│   ├── app_paths.py
│   ├── dependency_health.py
│   └── event_bus.py
├── backend_service/
│   ├── __init__.py
│   ├── api_app.py
│   ├── api_models.py
│   ├── websocket_events.py
│   ├── service_runner.py
│   └── auth.py
├── macos_app/
│   ├── Lulu.xcodeproj
│   ├── Lulu/
│   │   ├── App/
│   │   ├── Features/
│   │   │   ├── Assistant/
│   │   │   ├── Diagnostics/
│   │   │   ├── Settings/
│   │   │   └── PDFAudiobooks/
│   │   ├── Models/
│   │   ├── Services/
│   │   └── Resources/
│   └── Packaging/
├── scripts/
│   ├── build_backend_helper.sh
│   ├── package_macos_app.sh
│   └── notarize_macos_app.sh
├── tests/
│   ├── test_runtime_controller.py
│   ├── test_backend_service.py
│   └── ...
└── docs/
    └── implementation-plans/
```

### Boundary Rules

- existing core modules stay in place unless they are being cleanly extracted into `app_core/`
- `main.py` remains as the CLI entrypoint during the migration
- `terminal_ui.py` remains supported in developer mode
- `pdf_audiobook.py` remains a separate backend workflow even after GUI support exists

## Proposed Runtime Decomposition

### 1. `app_core/`

Purpose:

- host the reusable runtime orchestration layer that both the CLI and the desktop shell can call

Owns:

- startup and shutdown coordination
- mode changes
- dependency checks
- event emission
- app-path selection
- degraded-state transitions

Does not own:

- GUI rendering
- raw persistence semantics
- Ollama transport internals
- Chroma internals
- audio DSP internals

### 2. Existing Core Modules

The following stay backend-owned:

- `audio_handler.py`
- `wake_detection.py`
- `llm_router.py`
- `memory_manager.py`
- `ollama_client.py`
- `pdf_audiobook.py`

These modules may receive small interface changes so they can publish structured events, but they should not migrate into the GUI layer.

### 3. `backend_service/`

Purpose:

- expose the runtime controller over a versioned local API

Owns:

- HTTP routes
- request validation
- WebSocket event publishing
- auth token validation for local clients
- helper process lifecycle hooks

### 4. `macos_app/`

Purpose:

- provide native macOS UX for the same backend capabilities

Owns:

- windows and navigation
- onboarding
- dependency guidance
- settings forms
- diagnostics rendering
- notifications
- permissions prompts and app lifecycle

## API And IPC Design

### Process Model

The backend should run as a managed child process launched by the app shell.

Why this is the safest v1 choice:

- process isolation protects the app shell from backend crashes
- the backend can be restarted independently
- logs and diagnostics remain clear
- the same backend helper can also be driven by CLI/dev tools
- this avoids a higher-risk in-process Python embedding design

### Transport

Use:

- HTTP/JSON for command and query operations
- WebSocket for event streaming

Do not use as the primary contract:

- ad hoc stdio framing
- native Swift/Python bridge glue as the main API surface

### API Versioning

Every route and event schema should be versioned from day one:

- HTTP base path: `/v1`
- event envelope includes `api_version`

### Authentication

Use a per-launch bearer token generated by the app shell and passed to the backend helper at startup.

Rules:

- bind only to `127.0.0.1`
- never expose the backend on external interfaces
- reject requests without the launch token

## Service Endpoint Definitions

### Health And Dependency Endpoints

#### `GET /healthz`

Purpose:

- basic process liveness and readiness

Example response:

```json
{
  "api_version": "v1",
  "status": "ok",
  "service": "lulu-backend",
  "ready": true
}
```

#### `GET /v1/dependencies`

Purpose:

- return current dependency health for UI onboarding and diagnostics

Example response:

```json
{
  "api_version": "v1",
  "ollama": {
    "reachable": true,
    "host": "http://localhost:11434",
    "chat_model_available": true,
    "embedding_model_available": true
  },
  "audio_input": {
    "available": true,
    "device_name": "MacBook Pro Microphone"
  },
  "tts": {
    "available": true,
    "engine": "say"
  },
  "memory": {
    "available": true,
    "path": "/Users/example/Library/Application Support/Lulu/chroma"
  }
}
```

### Settings Endpoints

#### `GET /v1/settings`

Purpose:

- return effective runtime settings after merging persisted app config and environment overrides

#### `PUT /v1/settings`

Purpose:

- update mutable settings from the desktop app

Rules:

- validate all user-provided settings before persistence
- do not allow unsupported runtime mutations to silently apply
- include a response field indicating whether restart is required

Example request:

```json
{
  "chat_model": "llama3.2:3b",
  "embedding_model": "nomic-embed-text",
  "practical_voice_mode": true
}
```

Example response:

```json
{
  "api_version": "v1",
  "saved": true,
  "restart_required": false
}
```

### Runtime Control Endpoints

#### `POST /v1/runtime/start`

Purpose:

- start the assistant runtime in the selected mode

Example request:

```json
{
  "mode": "continuous"
}
```

#### `POST /v1/runtime/stop`

Purpose:

- stop active listening or worker activity gracefully

#### `POST /v1/runtime/restart`

Purpose:

- restart the runtime after failure, settings changes, or dependency recovery

#### `GET /v1/runtime/state`

Purpose:

- query the current high-level runtime state

Example response:

```json
{
  "api_version": "v1",
  "mode": "listening",
  "runtime_mode": "continuous",
  "status_line": "Waiting for 'hey lulu'...",
  "degraded": false,
  "last_error": ""
}
```

### Turn And Conversation Endpoints

Typed text-turn submission has been removed from the runtime surface. Conversation turns now enter through the existing voice pipeline only, with transcript, reply, memory, and TTS state continuing to arrive over the WebSocket event stream and runtime diagnostics snapshot.

#### `POST /v1/mode`

Purpose:

- switch between `continuous` and `turn-based`

Rules:

- preserve the existing runtime semantics from `main.py`
- reject unsupported mode transitions cleanly

### PDF Audiobook Endpoints

#### `POST /v1/pdf-audiobook/jobs`

Purpose:

- launch a separate offline audiobook job without merging it into the live assistant runtime

Example request:

```json
{
  "pdf_path": "/Users/example/Documents/book.pdf",
  "output_dir": "/Users/example/Documents/Lulu Exports",
  "voice": "Samantha",
  "format": "m4a",
  "dry_run": false
}
```

#### `GET /v1/pdf-audiobook/jobs/{job_id}`

Purpose:

- query job state, output paths, and failure details

## WebSocket Event Design

### Event Envelope

All streamed events should use a common envelope:

```json
{
  "api_version": "v1",
  "event_type": "runtime.state_changed",
  "timestamp": "2026-07-02T18:12:00Z",
  "payload": {}
}
```

### Required Event Types

- `runtime.state_changed`
- `dependency.health_changed`
- `wake.attempt`
- `wake.guidance_updated`
- `transcript.updated`
- `response.partial`
- `response.final`
- `router.invocation_updated`
- `memory.saved`
- `memory.recalled`
- `tts.chunk_emitted`
- `tts.chunk_spoken`
- `latency.snapshot`
- `error.reported`
- `pdf_audiobook.job_updated`

### Example Events

Wake attempt:

```json
{
  "api_version": "v1",
  "event_type": "wake.attempt",
  "timestamp": "2026-07-02T18:12:00Z",
  "payload": {
    "accepted": true,
    "score": 0.91,
    "reason": "score-match",
    "transcript": "hey lulu what time is it"
  }
}
```

Partial response:

```json
{
  "api_version": "v1",
  "event_type": "response.partial",
  "timestamp": "2026-07-02T18:12:03Z",
  "payload": {
    "request_id": "2e34bc6e-2e1b-45f6-94cd-7b2f3ce095e9",
    "text": "Sure, I will remember that"
  }
}
```

Latency snapshot:

```json
{
  "api_version": "v1",
  "event_type": "latency.snapshot",
  "timestamp": "2026-07-02T18:12:04Z",
  "payload": {
    "capture_ms": 1200,
    "stt_ms": 340,
    "router_ms": 880,
    "first_token_ms": 420,
    "first_spoken_ms": 690,
    "tts_total_ms": 2100,
    "total_ms": 4520
  }
}
```

## UI Requirements For The Desktop Client

### Main App Surfaces

The first-class desktop client should have these surfaces:

- Assistant
- Diagnostics
- Settings
- PDF Audiobooks

### Assistant View

Must support:

- voice mode
- clear state badges:
  - ready
  - listening
  - wake matched
  - transcribing
  - thinking
  - speaking
  - cooldown

Must display:

- live transcript
- partial and final assistant text
- recent memory actions
- high-level dependency state

### Diagnostics View

This should replace the terminal dashboard for normal end users while preserving its value for developers.

Must include:

- wake attempts and acceptance reasons
- latency breakdown
- current action flow
- memory save and recall indicators
- degraded-state notices
- dependency health
- logs or recent runtime events

### Settings View

Should include:

- chat model
- embedding model
- practical voice mode
- input device selection
- advanced wake settings
- storage locations
- export directory defaults

### PDF Audiobooks View

Must stay clearly separate from the live assistant runtime.

Should include:

- native file picker
- dry-run option
- export destination selection
- progress and result reporting
- clear errors for encrypted or image-only PDFs

## Packaging And Distribution Design

### App Bundle Shape

Target bundle:

```text
Lulu.app
└── Contents
    ├── MacOS/Lulu
    ├── Helpers/lulu-backend
    ├── Resources/backend/
    ├── Resources/defaults.json
    ├── Frameworks/
    └── Info.plist
```

### Python Runtime Strategy

Use a packaged backend helper rather than a repo-local virtualenv.

Requirements:

- relocatable
- signable as a nested executable
- launchable without a repo checkout
- able to locate bundled resources and app-owned writable paths

### Ollama Strategy

For v1:

- Ollama remains external
- Lulu detects whether Ollama is installed and reachable
- Lulu verifies required models
- Lulu can provide setup guidance and optionally try to start the local Ollama service if present

Do not bundle Ollama in v1.

### Writable App Paths

Move durable runtime files to app-owned paths:

- `~/Library/Application Support/Lulu/config.json`
- `~/Library/Application Support/Lulu/chroma/`
- `~/Library/Application Support/Lulu/logs/`
- `~/Library/Application Support/Lulu/exports/`
- `~/Library/Caches/Lulu/`

### Permissions And Entitlements

Required:

- `NSMicrophoneUsageDescription`

Initial distribution stance:

- hardened runtime enabled
- notarized Developer ID distribution
- no App Sandbox in v1

### Release Artifact

Use:

- signed and notarized `.dmg`

Defer:

- Mac App Store packaging
- `.pkg` installer unless later needed for system-level extras
- auto-update integration until the bundle layout stabilizes

## Phase 0 Goal

Phase 0 is the desktop-foundation extraction slice.

Goal:

- create a reusable backend core that can support both the current CLI and a future GUI without changing current product behavior

Phase 0 does not ship a polished GUI.

## Phase 0 File-By-File Change Plan

### `main.py`

Change:

- reduce `main.py` from primary orchestration owner to CLI entrypoint plus adapter

Work:

- extract runtime orchestration into a reusable `RuntimeController`
- keep CLI argument parsing and terminal-mode entry behavior
- replace direct state mutation with calls into `app_core`

Must preserve:

- current `text`, `turn-based`, and `voice` mode semantics
- current wake flow
- current shutdown behavior

### `terminal_ui.py`

Change:

- convert `TerminalUI` into an event consumer rather than the place runtime state is invented

Work:

- introduce a typed event-to-view-model adapter
- keep current visible panels and operator value
- ensure developer mode still renders wake, latency, memory, and action-flow signals

Must preserve:

- current observability depth
- current thread-safety guarantees

### `config.py`

Change:

- split configuration source concerns from configuration schema

Work:

- preserve environment-backed settings for current CLI/dev workflows
- add support for app-managed config file resolution
- add app-path aware defaults for logs, persistence, and exports

Must preserve:

- current environment-variable compatibility
- current validation behavior

### `audio_handler.py`

Change:

- keep ownership of audio capture, STT coordination, wake phrase text matching, and TTS

Work:

- add structured event emission hooks for:
  - capture started/completed
  - transcript updates
  - TTS chunk emitted/spoken
  - capture or TTS failures
- avoid moving audio logic into the GUI shell

Must preserve:

- current non-interruptible playback
- current capture and transcription behavior

### `wake_detection.py`

Change:

- no architectural rewrite

Work:

- expose structured wake diagnostics needed by the service event stream
- keep the hybrid acoustic scoring pipeline unchanged

Must preserve:

- current acoustic preprocessing and DTW matching behavior

### `llm_router.py`

Change:

- keep routing and memory/tool decisions backend-owned

Work:

- expose richer structured result objects for the runtime controller
- avoid UI-specific branching
- make save/recall events easier to serialize into the event stream

Must preserve:

- current explicit-save behavior
- current bounded tool orchestration
- current memory context injection behavior

### `memory_manager.py`

Change:

- keep Chroma and memory semantics backend-owned

Work:

- accept app-path aware storage roots
- expose structured save/recall metadata to the runtime controller and service layer

Must preserve:

- current local persistence behavior
- current auditable memory surfaces

### `ollama_client.py`

Change:

- keep Ollama transport local and backend-owned

Work:

- add dependency-health helpers if needed
- expose failure states in a service-friendly shape

Must preserve:

- current local-only transport behavior
- current model interaction semantics

### `pdf_audiobook.py`

Change:

- no merge into the live assistant runtime

Work:

- add callable job-oriented entrypoints suitable for a service layer
- return structured progress and result objects

Must preserve:

- OCR deferred
- rejection of encrypted or image-only PDFs
- current export behavior

### New `app_core/runtime_models.py`

Add:

- dataclasses or typed models for:
  - runtime state
  - dependency health
  - wake attempt
  - transcript event
  - memory event
  - latency snapshot
  - error event

### New `app_core/event_bus.py`

Add:

- a small internal event publisher/subscriber layer shared by CLI and service mode

Rules:

- keep it local and simple
- do not introduce a distributed event framework

### New `app_core/app_paths.py`

Add:

- helper functions to resolve:
  - repo/dev paths
  - app-support paths
  - log paths
  - cache paths
  - export paths

### New `app_core/runtime_controller.py`

Add:

- the primary reusable runtime orchestrator

Owns:

- startup and shutdown
- mode transitions
- dependency probing
- turn execution coordination
- event emission

### New `backend_service/api_models.py`

Add:

- request and response schemas
- event envelope models

### New `backend_service/api_app.py`

Add:

- HTTP routes and WebSocket handlers

### New `backend_service/service_runner.py`

Add:

- backend process bootstrap and local server startup

### New Tests

Add:

- `tests/test_runtime_controller.py`
- `tests/test_backend_service.py`
- path and config migration tests if those concerns are split out

The goal is not broad new coverage for coverage's sake.

The goal is to lock down the new service boundary and preserve current runtime behavior.

## Phase 0 Verification Plan

Phase 0 is complete only if all of the following are true:

1. existing backend-focused tests still pass:
   - wake
   - runtime resilience
   - TTS
   - memory
   - router
   - PDF workflow
2. new runtime-controller tests pass
3. new backend-service contract tests pass
4. the CLI still works through `main.py`
5. the runtime can answer a text turn through the new service API
6. no repo-root path is required when app-support paths are provided
7. the terminal UI still exposes the same practical observability for developers

## Implementation Notes And Guardrails

- prefer extraction over rewrite
- keep the first slice backend-first
- avoid introducing speculative abstraction layers
- do not move sensitive or stateful backend logic into the GUI shell for convenience
- do not collapse the PDF utility into the assistant transcript flow
- do not ship a GUI that hides degraded states or diagnostic details the terminal currently exposes

## Documentation That Should Be Updated When Phase 0 Starts

- `docs/architecture.md`
- `docs/operations.md`
- `docs/prd.md`
- `docs/decision-log.md`
- `README.md`
- `ROADMAP.md`

## Explicit Non-Goals For Phase 0

- polished end-user GUI
- bundling Ollama
- App Store sandbox compatibility
- playback interruption
- replacing the current audio stack
- replacing the current memory system
- replacing the current wake system
- changing PDF OCR scope

## Phase 0 Exit Criteria

Phase 0 should be considered successful when:

- the same backend runtime core can drive the terminal UI and a future GUI
- a stable local API exists for text turns, dependency health, settings, and runtime lifecycle
- app-owned paths work without a repo checkout
- no core product guarantees have regressed

## Recommended Next Slice After Phase 0

If Phase 0 succeeds, the next slice should be:

- a thin SwiftUI shell that can:
  - launch the backend helper
  - show dependency health
  - submit text turns
  - display transcript, response, and diagnostics

That slice should still avoid full voice-mode parity until the backend service contract has proven stable.
