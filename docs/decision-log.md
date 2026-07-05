# Lulu VAIA Decision Log

## Purpose

This document is the centralized, version-controlled record of strategic and technical decisions for Lulu VAIA.

It is intended to support:

- product and engineering alignment
- implementation continuity across future iterations
- auditability for why key choices were made
- controlled updates when decisions evolve over time

Related documents:

- [Documentation Index](./README.md)
- [Product Requirements Document](./prd.md)
- [Original Project Blueprint](../Project_Blueprint_AI_Assistant.md)

## Standardized Entry Format

Each decision entry uses the same fields:

- Decision ID
- Title
- Status
- Date
- Last Updated
- Context
- Stakeholders / Review Participants
- Options Considered
- Decision
- Rationale
- Tradeoffs
- Revision History
- Evidence

## Dating And Status Notes

- This document is a retrospective reconstruction created on `2026-07-01` from repository evidence.
- Unless otherwise noted, `Date` and `Last Updated` refer to the documentation date of this reconstructed entry, not a guaranteed original decision approval timestamp.
- `Status` combines decision state and delivery state using the current repo baseline, for example `Accepted and shipped` or `Accepted and intentionally deferred`.

## Evidence Conventions

- Evidence points to repository artifacts that support the entry, such as implementation plans, source files, tests, and git history.
- This initial version uses repo-relative documentation evidence. Future updates should append commit SHAs, issue links, or narrower section references when decisions change.

## Stakeholder Note

The repository does not include a formal review roster. Where named individuals are not recorded in the source evidence, stakeholders are listed by role to avoid fabricating undocumented participants.

## Global Revision History

| Date | Version | Change |
| --- | --- | --- |
| 2026-07-01 | 1.0 | Initial centralized decision log created from repository evidence |

---

## D-001 Local-First Apple Silicon Runtime

- Decision ID: `D-001`
- Title: Local-First Apple Silicon Runtime
- Status: Accepted and shipped
- Date: 2026-07-01
- Last Updated: 2026-07-01

### Context

Lulu was conceived as a local assistant for a Mac M1 workflow. The product needed strong privacy guarantees, low setup complexity, and practical latency on Apple Silicon without relying on NVIDIA or cloud infrastructure.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Fully local Apple Silicon stack with MLX, Ollama, ChromaDB, and native macOS TTS
2. Cloud-hosted inference and storage
3. Local runtime based on CUDA or PyTorch/CUDA dependencies

### Decision

Adopt a fully local, Apple Silicon-first runtime built around `mlx-whisper`, Ollama, ChromaDB, and macOS `say`.

### Rationale

This approach best matches the project's privacy, platform, and operational constraints while minimizing infrastructure dependencies.

### Tradeoffs

- Benefits:
  - strong privacy posture
  - simpler local development workflow
  - no cloud service dependency for core functionality
- Costs:
  - limited to hardware and models that run well locally
  - fewer managed-service conveniences
  - stronger dependence on Apple Silicon performance characteristics

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |

### Evidence

- `Project_Blueprint_AI_Assistant.md`
- `README.md`
- `requirements.txt`

---

## D-002 Native Ollama API Contract

- Decision ID: `D-002`
- Title: Use Native Ollama Endpoints And Tool Format
- Status: Accepted and shipped
- Date: 2026-07-01
- Last Updated: 2026-07-01

### Context

Lulu requires chat generation, embeddings, and tool-calling support. The product needed a stable backend contract for local inference that matched the real runtime surface being used in production.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Use Ollama native endpoints: `/api/chat`, `/api/embed`, and native `tool_calls`
2. Use only the OpenAI-compatible `/v1` layer
3. Abstract the provider interface before shipping the first working product

### Decision

Standardize on the native Ollama API contract and native tool-call handling.

### Rationale

The native contract removes ambiguity around tool-calling behavior and keeps the implementation aligned with the local model runtime actually being used.

### Tradeoffs

- Benefits:
  - fewer translation layers
  - clearer tool-call semantics
  - better alignment with Ollama-native behavior
- Costs:
  - tighter provider coupling
  - additional migration effort if another local runtime is adopted later

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |

### Evidence

- `README.md`
- `ollama_client.py`

---

## D-003 Hybrid Router With Explicit Save Bypass

- Decision ID: `D-003`
- Title: Split Deterministic Memory Saves From Conversational Turns
- Status: Accepted and shipped
- Date: 2026-07-01
- Last Updated: 2026-07-02

### Context

The product needed to support both direct user-controlled memory writes and autonomous memory capture during normal conversation. A single conversational path would add unnecessary latency and ambiguity for explicit saves.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Explicit `insert info` bypass plus separate conversational tool-call path
2. Force all memory writes through the conversational model
3. Provide only manual memory writes with no autonomous save tool

### Decision

Use a hybrid router with two paths:

- explicit `insert info` bypass for deterministic saves
- normal conversational path with optional `save_to_memory` tool use through a validated backend tool registry

### Rationale

This preserves user control, reduces compute on deterministic save requests, and still allows agentic memory capture when users speak naturally. The registry-backed tool path keeps the safety boundary in Python by separating tool metadata, schema validation, and execution while allowing a bounded memory-focused tool surface instead of a single hardcoded save path. The tool surface now also includes read-only recency lookup and auditable explanation of returned memory ids, while the terminal UI surfaces whether a turn stayed chat-only, used the explicit save command, or ran the validated backend tool path so users can understand what Lulu actually did.

### Tradeoffs

- Benefits:
  - faster explicit saves
  - clear mental model
  - preserves autonomous memory behavior
- Costs:
  - more routing logic
  - more documentation and test surface

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |
| 2026-07-02 | Replaced the hardcoded tool execution path with a validated registry-backed contract while keeping the single-tool operating model |
| 2026-07-02 | Added user-visible invocation-path and tool-status surfaces to reduce ambiguity between chat, explicit saves, and validated tool use |
| 2026-07-02 | Expanded the validated memory tool surface to bounded multi-tool rounds with read-only conversational lookup |
| 2026-07-02 | Added recent-memory and explain-by-id tools for safer conversational memory inspection |

### Evidence

- `Project_Blueprint_AI_Assistant.md`
- `README.md`
- `llm_router.py`
- `tests/test_llm_router.py`

---

## D-004 Canonical Memory Store With Semantic Deduplication

- Decision ID: `D-004`
- Title: Prefer Canonical Memory Records Over Raw Append-Only Memory
- Status: Accepted and shipped
- Date: 2026-07-01
- Last Updated: 2026-07-02

### Context

Raw append-only memory would accumulate duplicates and reduce recall quality over time. The product needed a memory model that stayed useful during repeated conversations while remaining lightweight.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Canonical record model with semantic deduplication, backend tags, and latest-wins updates
2. Append-only memory entries with no merge behavior
3. Fully structured taxonomy and schema-enforced memory model from day one

### Decision

Use canonical memory records, semantic deduplication, backend-assigned free-form tags, and latest-wins updates for conflicting facts in the same semantic slot. Track auditable metadata such as primary category, source label, revision count, and update timestamps on each canonical record.

### Rationale

This improves long-term memory quality without prematurely imposing a rigid schema or expensive manual review workflow, while still giving the assistant and operator enough provenance to inspect why a memory exists and how many times it has changed.

### Tradeoffs

- Benefits:
  - cleaner recall context
  - lower duplicate noise
  - simple tool interface for the model
- Costs:
  - no detailed end-user revision trail yet
  - free-form tags can drift without future governance

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |
| 2026-07-02 | Added auditable category, source-label, revision-count, and update-time metadata to canonical records |

### Evidence

- `.trae/documents/a1-memory-deduplication-and-categories-plan.md`
- `memory_manager.py`
- `tests/test_memory_manager.py`

---

## D-005 Observability-First Terminal Dashboard

- Decision ID: `D-005`
- Title: Use A Rich Terminal Dashboard As The Primary Operator Surface
- Status: Accepted and shipped
- Date: 2026-07-01
- Last Updated: 2026-07-01

### Context

The product needed a fast feedback surface for iterative development and runtime calibration. A GUI would have added delivery cost before the core voice loop and wake mechanics were stable.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. `rich`-based terminal dashboard with live state and diagnostics
2. Minimal console logging only
3. Full GUI before stabilizing the core runtime

### Decision

Treat the terminal dashboard as a first-class product and engineering surface for operating Lulu.

### Rationale

This provides immediate visibility into voice capture, routing, memory, streaming, and wake behavior while keeping implementation complexity low.

### Tradeoffs

- Benefits:
  - high debugging value
  - fast iteration cycle
  - low implementation overhead
- Costs:
  - less polished than a graphical application
  - less accessible to non-technical users

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |

### Evidence

- `README.md`
- `terminal_ui.py`
- `.trae/documents/b1-chunked-tts-streaming-plan.md`
- `.trae/documents/c1-wake-word-continuous-listening-plan.md`

---

## D-006 Stream Replies In Smoother Grouped Chunks On Native macOS TTS

- Decision ID: `D-006`
- Title: Stream Replies In Smoother Grouped Chunks While Keeping Native `say`
- Status: Accepted and shipped
- Date: 2026-07-01
- Last Updated: 2026-07-02

### Context

The product needed lower perceived latency than full-response playback, but it also needed to preserve the low-friction setup of native macOS speech output.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Smaller phrase-boundary streaming on top of macOS `say`
2. Wait for full response completion before starting TTS
3. Replace native TTS immediately with a higher-fidelity local engine

### Decision

Begin playback through smoother grouped chunks while retaining macOS `say` for the current release.

### Rationale

This keeps the native zero-setup TTS path while reducing chopped playback by buffering the first spoken chunk, grouping short neighboring sentences, preferring clause-aware breaks before hard splits, merging tiny final tails backward when safe, and exposing continuity signals in the dashboard for live tuning.

### Tradeoffs

- Benefits:
  - much smoother end-of-sentence playback than smaller phrase chunks
  - still starts before full-response completion
  - no new heavyweight dependency
  - modular upgrade path for later TTS replacement
- Costs:
  - first spoken output is slightly later than the original latency-first policy
  - some seams can still remain because native `say` is restarted per chunk
  - voice quality remains limited by native `say`

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |
| 2026-07-02 | Revised to smoothness-first grouped playback with tail-merge protection |
| 2026-07-02 | Added clause-aware fallback chunking and continuity metrics for live tuning |

### Evidence

- `.trae/documents/b1-chunked-tts-streaming-plan.md`
- `audio_handler.py`
- `config.py`
- `main.py`
- `terminal_ui.py`
- `tests/test_streaming_tts.py`

---

## D-007 Keep Playback Non-Interruptible For The Current Release

- Decision ID: `D-007`
- Title: Defer Barge-In And Keep Playback Non-Interruptible
- Status: Accepted and intentionally deferred
- Date: 2026-07-01
- Last Updated: 2026-07-01

### Context

Interruptible speech adds concurrency, cancellation, and turn-control complexity. The core product still needed stable streaming and wake behavior before introducing barge-in.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Keep playback non-interruptible for the current release
2. Add full barge-in during the first streaming implementation
3. Avoid streaming entirely until interruption support exists

### Decision

Keep the current speech pipeline non-interruptible and defer barge-in to a later milestone.

### Rationale

This constrains the state machine and keeps the current release focused on reliable local voice response.

### Tradeoffs

- Benefits:
  - simpler runtime control
  - fewer race conditions
  - faster delivery of core streaming behavior
- Costs:
  - less natural conversational turn-taking
  - users cannot cut off long responses

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |

### Evidence

- `README.md`
- `.trae/documents/b1-chunked-tts-streaming-plan.md`
- `.trae/documents/c1-wake-word-continuous-listening-plan.md`

---

## D-008 Transcript-Gated Wake Detection With Cooldown And Self-Audio Guard

- Decision ID: `D-008`
- Title: Use Hybrid Acoustic Plus Transcript Wake Matching Instead Of A Dedicated Wake Model
- Status: Accepted and shipped
- Date: 2026-07-01
- Last Updated: 2026-07-02

### Context

Lulu needed an always-on interaction model but also had to remain local, lightweight, and aligned with the existing STT-centric pipeline. A dedicated wake-word engine would add another model or subsystem to maintain.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Acoustic preprocessing plus DTW wake scanning, transcript confirmation, cooldown, and transcript similarity guard
2. Exact-match wake detection only
3. Dedicated wake-word model plus separate echo-cancellation stack

### Decision

Use short passive captures, local acoustic preprocessing, MFCC/spectral feature extraction, DTW-assisted wake scoring, transcript confirmation when needed, a fixed follow-up window, cooldown, and recent-speech similarity suppression.

### Rationale

This approach preserves the local stack, adds a low-latency acoustic stage before Whisper, handles common pronunciation drift and noisy-room captures more robustly, and stays observable enough to calibrate through the terminal dashboard with live guidance, rejection summaries, signal metrics, and an optional practical voice preset for more forgiving day-to-day use.

### Tradeoffs

- Benefits:
  - stays within the existing architecture
  - adds a sub-200 ms acoustic wake candidate path without introducing a separate model runtime
  - easier to debug than a more opaque subsystem
  - tolerates common STT mis-hears
- Costs:
  - wake quality still depends partly on transcription quality when inline requests must be extracted
  - threshold tuning remains product-specific
  - echo suppression remains single-channel and heuristic, not true reference-based acoustic echo cancellation

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-01 | Initial entry recorded |
| 2026-07-02 | Added practical voice preset and richer wake guidance metrics to the shipped operating model |
| 2026-07-02 | Added acoustic preprocessing, DTW wake scoring, fast-path wake acceptance, and synthetic benchmark evidence |

### Evidence

- `.trae/documents/c1-wake-word-continuous-listening-plan.md`
- `audio_handler.py`
- `main.py`
- `terminal_ui.py`
- `tests/test_continuous_listening.py`
- `wake_detection.py`
- `wake_benchmark.py`
- `tests/test_wake_benchmark.py`

---

## D-009 Extract A Reusable Runtime Core And Local Service Boundary

- Decision ID: `D-009`
- Title: Prepare The Python Backend For A Native macOS App Through Extraction And Local IPC
- Status: Accepted and in progress
- Date: 2026-07-02
- Last Updated: 2026-07-02

### Context

Lulu's current runtime was originally optimized for the terminal dashboard and repo-local shell scripts. The macOS desktop app direction requires a stable backend that can be reused by both the existing CLI and a future native GUI without rewriting the core assistant logic in another stack.

### Stakeholders / Review Participants

- Product sponsor / repository owner
- Implementation authoring agent

### Options Considered

1. Keep `main.py` as the only runtime surface and let the future GUI reimplement orchestration around it
2. Extract a reusable backend core and expose it through a loopback-only authenticated local service
3. Rewrite the backend in a GUI-native stack before packaging

### Decision

Adopt a staged migration path that:

- extracts runtime orchestration into reusable backend-owned modules under `app_core/`
- keeps the current CLI path working through `main.py`
- adds an authenticated loopback-only HTTP and WebSocket service under `backend_service/`
- preserves Python ownership of wake, memory, router, Ollama, and TTS behavior

### Rationale

This path preserves the current product guarantees while creating a stable seam for a future SwiftUI shell. It reduces the chance that the GUI will fork backend logic, keeps observability grounded in backend-emitted events, and avoids a risky rewrite away from the repo's Python-first architecture.

### Tradeoffs

- Benefits:
  - preserves current runtime behavior while enabling desktop packaging work
  - gives both the CLI and future GUI a shared backend authority
  - keeps the service boundary local, authenticated, and testable
- Costs:
  - adds FastAPI and Uvicorn to the dependency surface
  - introduces another execution mode to document and maintain
  - requires continued discipline so the terminal UI remains a consumer, not a second runtime owner

### Revision History

| Date | Change |
| --- | --- |
| 2026-07-02 | Initial entry recorded during Stage 0 and Stage 1 desktop migration work |

### Evidence

- `.trae/documents/macos-desktop-app-staged-implementation-plan.md`
- `app_core/`
- `backend_service/`
- `main.py`
- `tests/test_runtime_controller.py`
- `tests/test_backend_service.py`
- `tests/test_pdf_audiobook_service_contract.py`
