# Codebase Audit - 2026-07-06

## Scope

- Repository root: `/Users/home/Lulu-VAIA/lulu`
- Audit date: `2026-07-06`
- Application shape:
  - Python voice runtime and local FastAPI helper
  - Swift macOS desktop shell under `macos_app/`
  - Chroma-backed local memory store
  - Offline PDF-to-audiobook pipeline
- Targeted high-risk review areas:
  - authentication and local IPC
  - persistence and configuration loading
  - subprocess execution and file handling
  - dependency and supply-chain exposure
- Payment workflow review:
  - no payment or billing implementation was found in the repository

## Audit Inputs

### Documentation And Setup Reviewed

- `README.md`
- `docs/README.md`
- `docs/architecture.md`
- `docs/operations.md`
- `docs/prd.md`
- `pyproject.toml`
- `requirements.txt`
- `requirements-dev.txt`
- `macos_app/README.md`

### Automated Scans

- `semgrep --config auto --json --output .tmp/audit-20260706/semgrep-auto.json .`
- `.venv/bin/ruff check . --output-format json > .tmp/audit-20260706/ruff.json`
- `.venv/bin/bandit -r . -f json -o .tmp/audit-20260706/bandit.json -x ./.venv,./tests,./.tmp,./macos_app/.build`
- `.venv/bin/pip-audit -r requirements.txt -f json -o .tmp/audit-20260706/pip-audit.json`

### Raw Artifacts

- `.tmp/audit-20260706/semgrep-auto.json`
- `.tmp/audit-20260706/ruff.json`
- `.tmp/audit-20260706/bandit.json`
- `.tmp/audit-20260706/pip-audit.json`

These JSON files are the complete searchable inventory for automated findings. The sections below consolidate high-value issues, add manual-review findings, remove false positives, and assign business/security severity.

## Remediation Status

- Current codebase status as of the low-gap closure wave:
  - `High` findings `F-01` through `F-02`: remediated in the current tree
  - `Medium` findings `F-03` through `F-11`: remediated or explicitly triaged in the current tree
  - `Low` findings `F-12` through `F-14`: remediated in the current tree
- Deferred follow-up:
  - repo-wide Ruff hygiene cleanup remains a separate low-severity backlog, dominated by `E501` plus older import-order and unused-import debt
- Historical note:
  - the findings below remain preserved as the original audit record, even where the implementation has since been corrected

## Coverage Summary

- Semgrep: 73 tracked files scanned, parsed lines approximately 100%, 3 findings
- Ruff: 149 findings across 30 files
- Bandit: 10 findings across 2 files
- pip-audit: 1 vulnerable package reported
- Swift static validation:
  - `swift build` could not complete in the sandbox because SwiftPM manifest evaluation was blocked by `sandbox-exec: sandbox_apply: Operation not permitted`
  - Swift source was therefore reviewed manually

### Ruff Breakdown

- `E501`: 107
- `I001`: 17
- `F401`: 17
- `B905`: 4
- `E402`: 3
- `F841`: 1

### Bandit Breakdown

- `B404`: 2
- `B603`: 4
- `B607`: 2
- `B615`: 2

## Severity Model

- Security issues:
  - `Critical`: direct compromise of host, secrets, or privileged control with low attacker effort
  - `High`: realistic exploitation with material confidentiality, integrity, or availability impact
  - `Medium`: constrained exploit path, local-only abuse, or meaningful denial-of-service / data-integrity risk
  - `Low`: hygiene, defense-in-depth, or non-default exploit preconditions
- Functional and quality issues:
  - `High`: durable data loss, incorrect control-plane behavior, or workflow-breaking faults
  - `Medium`: reliability or correctness gaps with user-visible impact
  - `Low`: maintainability, readability, or low-risk robustness problems

## False Positives And Low-Signal Tool Output

- Semgrep `detect-insecure-websocket` on `ws://127.0.0.1` is not a direct transport-security bug by itself because the design is local loopback IPC rather than remote network transport. The real issue is token handling and endpoint trust, captured separately below.
- Semgrep `IFS tampering` on `scripts/install_lulu.sh` and `scripts/start_lulu.sh` is low signal here. The scripts consistently quote expansions and do not rely on dangerous unquoted splitting patterns in the audited paths.
- Bandit `B404`, `B603`, and most `B607` hits are generic subprocess warnings. They are relevant as review prompts, but only become actionable where command resolution, timeout behavior, or untrusted paths create a real risk.

## Prioritized Findings

### F-01 - Loopback Port Hijack Can Steal The Launch Token

- Type: Security vulnerability
- Severity: High
- Confidence: High
- Owner: Desktop + Backend
- Files:
  - `macos_app/Sources/LuluApp/Services/BackendConfiguration.swift`
  - `macos_app/Sources/LuluApp/Services/BackendServiceCoordinator.swift`
- Evidence:
  - `BackendConfiguration.resolveLaunchPort()` does a bind-close availability check and returns a port that is later reused by the backend process.
  - `BackendServiceCoordinator.waitUntilHealthy()` trusts the first process that answers `GET /healthz`.
  - Every request includes `Authorization: Bearer <launchToken>`.
- Code references:
  - `BackendConfiguration.swift:20-46`
  - `BackendConfiguration.swift:107-172`
  - `BackendServiceCoordinator.swift:48-79`
  - `BackendServiceCoordinator.swift:118-123`
  - `BackendServiceCoordinator.swift:213-217`
- Impact:
  - A local attacker can race-bind the selected loopback port before the Python backend starts, receive the bearer token on the first health probe, impersonate the helper, and access all privileged backend routes and event traffic.
- Proof of exploitability:
  - Attack sketch:
    1. Observe or predict the chosen port.
    2. Bind a fake loopback listener before the helper finishes starting.
    3. Return a healthy JSON payload on `/healthz`.
    4. Capture the desktop app's `Authorization` header.
    5. Reuse the stolen token against `/v1/settings`, `/v1/runtime/*`, `/v1/pdf-audiobook/jobs`, and `/v1/events/ws`.
- Remediation:
  - Replace bind-close port probing with an ownership-preserving startup design:
    - pre-bind the listening socket and pass the descriptor into the server process, or
    - use a one-shot handshake secret over stdio or a local file descriptor channel, or
    - launch on port `0` and communicate the actual bound port back through the child process output or pipe after bind succeeds
  - Add an authenticated readiness handshake that proves the responder is the launched child, not merely a process on the same port.
- Required tests:
  - Integration test that simulates a fake loopback listener on the preferred port and verifies the desktop client refuses it.
  - Regression test for backend startup with dynamically allocated ports and explicit child identity confirmation.

### F-02 - Corrupt Settings Files Are Silently Overwritten, Causing Durable Data Loss

- Type: Functional bug / data integrity
- Severity: High
- Confidence: High
- Owner: Backend
- Files:
  - `backend_service/api_app.py`
  - `config.py`
- Code references:
  - `api_app.py:154-168`
  - `config.py:21-31`
- Impact:
  - If the persisted JSON settings file is malformed, the backend silently treats it as empty and rewrites it with only the incoming update payload. This destroys unrelated stored settings instead of surfacing a repairable error.
- Proof:
  - The update path explicitly catches `JSONDecodeError` and falls back to `{}` before writing the merged file.
  - Reproduced from the audited logic:

```text
Input config: {bad json
Update payload: {"chat_model": "new-model"}
Resulting file:
{
  "chat_model": "new-model"
}
```

- Remediation:
  - Fail closed on malformed JSON with a `409` or `422` response and preserve the original file.
  - Write a `.bak` snapshot before any successful settings update.
  - Validate the full settings payload against a schema before persisting.
- Required tests:
  - Unit test for malformed config update returning an error without rewriting the file.
  - Integration test that ensures valid settings survive partial updates.
  - Regression test for backup creation and rollback on write failure.

### F-03 - WebSocket Auth Token Is Exposed In The URL Query String

- Type: Security vulnerability
- Severity: Medium
- Confidence: High
- Owner: Desktop + Backend
- Files:
  - `macos_app/Sources/LuluApp/Services/BackendConfiguration.swift`
  - `macos_app/Sources/LuluApp/Services/BackendServiceCoordinator.swift`
  - `backend_service/auth.py`
- Code references:
  - `BackendConfiguration.swift:44-46`
  - `BackendServiceCoordinator.swift:182-217`
  - `auth.py:34-49`
- Impact:
  - The launch token is duplicated in the WebSocket URL even though the desktop client already sends it in the `Authorization` header.
  - URL-based secrets are more likely to leak through logs, crash reports, diagnostics, request inspection tools, and future telemetry.
  - The token is interpolated directly into the URL string and is not percent-encoded.
- Proof of exploitability:
  - Any logging surface that records the full request URL exposes the bearer token with no additional access.
  - A token containing reserved URL characters can also break connection parsing or be misread by downstream tooling.
- Remediation:
  - Remove query-string token support from the client and server.
  - Accept header-based bearer auth only for WebSocket upgrades.
  - If a fallback is unavoidable, percent-encode tokens and gate the fallback behind a debug-only build flag.
- Required tests:
  - Unit test that rejects WebSocket connections with query-token auth.
  - Integration test that confirms header-only auth still succeeds.
  - Regression test with tokens containing reserved characters.

### F-04 - Export Playback Allows Manifest Path Traversal Outside The Export Root

- Type: Security vulnerability
- Severity: Medium
- Confidence: High
- Owner: Runtime / PDF pipeline
- Files:
  - `pdf_audiobook.py`
- Code references:
  - `pdf_audiobook.py:1132-1173`
  - `pdf_audiobook.py:1176-1227`
- Impact:
  - A crafted `manifest.json` can reference `../` paths outside the export directory.
  - `play_export_directory()` then passes those files to `say -f` or `afplay`, causing Lulu to read or play arbitrary local files chosen by the manifest author.
- Proof of exploitability:
  - Example payload in `manifest.json`:

```json
{
  "audio_outputs": {
    "portable_conversion": {
      "files": ["../outside-secret.txt"]
    }
  }
}
```

  - `_resolve_relative_paths()` accepts the path as long as `(output_dir / value).exists()`.
- Remediation:
  - Resolve each candidate path and enforce `candidate.is_relative_to(export_dir.resolve())`.
  - Reject absolute paths and any path containing parent traversal after normalization.
  - Treat malformed manifests as user errors instead of attempting best-effort playback.
- Required tests:
  - Unit tests for `../`, absolute paths, symlinks, and valid in-root paths.
  - Integration test ensuring playback refuses out-of-root assets.

### F-05 - Event Streaming Uses An Unbounded Queue Per WebSocket Client

- Type: Security vulnerability / availability
- Severity: Medium
- Confidence: High
- Owner: Backend
- Files:
  - `backend_service/websocket_events.py`
  - `app_core/runtime_controller.py`
- Code references:
  - `websocket_events.py:25-49`
  - `runtime_controller.py:1130-1143`
  - `runtime_controller.py:1173-1207`
- Impact:
  - Each connected client gets an unbounded `asyncio.Queue`.
  - High-frequency events such as `response.partial` and `tts.chunk_emitted` can accumulate indefinitely behind a slow or paused authenticated client, causing memory growth and eventual process instability.
- Proof of exploitability:
  - Connect an authenticated WebSocket client, stop reading frames, and trigger a long streamed response. The producer continues enqueueing events without backpressure or eviction.
- Remediation:
  - Use a bounded queue with a maximum length.
  - Drop or coalesce low-priority stream events when the queue is full.
  - Disconnect clients that stay over the high-water mark.
- Required tests:
  - Async test for slow-consumer overflow behavior.
  - Regression test confirming bounded memory usage under long streaming responses.

### F-06 - PDF Job Creation Has No Concurrency Limit Or Retention Policy

- Type: Security vulnerability / availability
- Severity: Medium
- Confidence: High
- Owner: Backend + Runtime
- Files:
  - `backend_service/api_app.py`
- Code references:
  - `api_app.py:34-80`
  - `api_app.py:204-212`
- Impact:
  - Each request spawns a daemon thread immediately.
  - Job state accumulates in memory forever.
  - An authenticated caller can exhaust threads, CPU, disk, and memory by flooding heavy PDF jobs.
- Proof of exploitability:
  - Repeated POSTs to `/v1/pdf-audiobook/jobs` create unbounded threads and persistent in-memory job records with no cap and no garbage collection.
- Remediation:
  - Replace ad hoc daemon threads with a bounded work queue and worker pool.
  - Add per-client or global concurrency caps.
  - Add job retention TTL and eviction of completed job metadata.
- Required tests:
  - Integration test that rejects new jobs beyond the cap.
  - Regression test for cleanup of completed job records.

### F-07 - External Commands Can Hang Forever Because They Have No Timeouts

- Type: Reliability bug / availability
- Severity: Medium
- Confidence: High
- Owner: Runtime
- Files:
  - `audio_handler.py`
  - `pdf_audiobook.py`
- Code references:
  - `audio_handler.py:216-267`
  - `pdf_audiobook.py:983-1019`
  - `pdf_audiobook.py:1022-1075`
  - `pdf_audiobook.py:1145-1173`
- Impact:
  - `say`, `ffmpeg`, and `afplay` are invoked without `timeout=...`.
  - If a child process hangs, the queue worker and synchronous playback paths can block indefinitely.
  - `MacOSTTS.finish_turn()` waits on `queue.join()`, which means a single stuck child can stall voice output and shutdown behavior.
- Remediation:
  - Add per-command timeouts and convert `TimeoutExpired` into domain-specific errors.
  - Surface timeout diagnostics to the UI and logs.
  - Consider watchdog cancellation for long-running batch export jobs.
- Required tests:
  - Unit test that mocks `subprocess.run` raising `TimeoutExpired`.
  - Integration test for graceful failure and queue recovery after a child-process timeout.

### F-08 - `Settings(config_path=...)` Does Not Control Where Settings Are Loaded From

- Type: Functional bug / configuration correctness
- Severity: Medium
- Confidence: High
- Owner: Runtime
- Files:
  - `config.py`
  - `app_core/app_paths.py`
- Code references:
  - `config.py:21-52`
  - `config.py:97-123`
  - `app_paths.py:39-45`
- Impact:
  - The `Settings.config_path` field can point at one file while the rest of the settings are loaded from `default_config_path()`.
  - This is a confusing split-brain configuration model and can make operators believe a custom config file is active when it is not.
- Proof:
  - Reproduced during audit:

```text
config_path= /tmp/.../custom.json
expected custom= /tmp/.../custom.json
chroma_path= /tmp/from-env-default
```

  - The instance advertises one config path while actual values come from another source.
- Remediation:
  - Refactor settings loading so one resolved config source is chosen first and then used for all field lookups.
  - Remove `_app_config()` global state from field default factories.
- Required tests:
  - Unit test proving a supplied `config_path` becomes the source of truth for all settings.
  - Regression test for precedence order: explicit config path > env vars > defaults, or whatever order is intentionally chosen.

### F-09 - Memory Deduplication Overwrites Distinct Facts Based On Similarity Alone

- Type: Functional bug / data integrity
- Severity: Medium
- Confidence: High
- Owner: Memory / Runtime
- Files:
  - `memory_manager.py`
  - `tests/test_memory_manager.py`
- Code references:
  - `memory_manager.py:129-161`
  - `memory_manager.py:289-333`
  - `tests/test_memory_manager.py:136-156`
- Impact:
  - Semantically related but conflicting facts are merged into a single canonical record.
  - This destroys historical truth and prevents reliable long-term memory behavior.
- Example:
  - `"My favorite tea is jasmine"` followed by `"My favorite tea is mint"` collapses into one updated record.
- Remediation:
  - Require stronger duplicate semantics than embedding similarity alone.
  - Preserve revisions as a history chain or separate records when facts conflict.
  - Consider conflict-aware update rules keyed by subject + slot, not raw similarity.
- Required tests:
  - Unit tests for contradictory facts, same-subject updates, and true duplicates.
  - Regression test ensuring the prior value is preserved when the new fact conflicts.

### F-10 - Whisper Model Downloads Are Not Revision-Pinned

- Type: Supply-chain security
- Severity: Medium
- Confidence: Medium
- Owner: Runtime / Release
- Files:
  - `audio_handler.py`
- Code references:
  - `audio_handler.py:472-493`
- Source:
  - Bandit `B615`
- Impact:
  - If `settings.whisper_model` points at a remote Hugging Face repository, Lulu fetches the latest contents without revision pinning.
  - This weakens reproducibility and raises supply-chain risk if the upstream artifact changes.
- Reachability note:
  - This is real code, not a false positive.
  - It is lower risk than F-01 because it depends on model download behavior and repository trust, not routine local IPC.
- Remediation:
  - Pin the expected model revision or digest.
  - Restrict allowed repositories to a vetted allowlist.
  - Record the resolved revision in diagnostics.
- Required tests:
  - Unit test that remote model references require a pinned revision.
  - Regression test for deterministic warmup after cache miss.

### F-11 - ChromaDB Dependency Advisory Requires Triage

- Type: Dependency vulnerability
- Severity: Medium
- Confidence: Medium
- Owner: Release / Security
- Package:
  - `chromadb 1.5.9`
- Advisory:
  - `PYSEC-2026-311`
  - `CVE-2026-45829`
  - `GHSA-f4j7-r4q5-qw2c`
- Impact:
  - Reported as pre-auth code injection in ChromaDB server endpoints involving malicious model repositories and `trust_remote_code=true`.
- Reachability note:
  - The current codebase uses `chromadb.PersistentClient(...)` in-process in `memory_manager.py:71-75`.
  - No Chroma HTTP server exposure was identified in this repository.
  - Treat as an important dependency risk, but not as a confirmed directly reachable exploit in the current architecture.
- Remediation:
  - Upgrade to a fixed Chroma release once available.
  - Document why the vulnerable server-only path is or is not reachable in this app.
  - Add dependency auditing to CI so future advisories block merges until triaged.

### F-12 - Diagnostics Log Capture Can Block On A Live Pipe

- Type: Reliability bug
- Severity: Low
- Confidence: Medium
- Owner: Desktop
- Files:
  - `macos_app/Sources/LuluApp/Services/BackendServiceCoordinator.swift`
  - `macos_app/Sources/LuluApp/App/AppModel.swift`
- Code references:
  - `BackendServiceCoordinator.swift:87-95`
- Impact:
  - `availableData` on a live pipe can block waiting for data or EOF, which can stall diagnostic refresh behavior.
- Remediation:
  - Switch to asynchronous pipe readers or buffered non-blocking log collection.
  - Keep diagnostics refresh off the main interaction path.

### F-13 - Memory Path Health Check Reports Existence, Not Real Writability

- Type: Quality / observability bug
- Severity: Low
- Confidence: High
- Files:
  - `app_core/dependency_health.py`
- Code references:
  - `dependency_health.py:62-65`
- Impact:
  - `memory_path_available` can report healthy for read-only or otherwise unusable paths, reducing the value of preflight diagnostics.
- Remediation:
  - Perform a real writability probe or an initialization check against the Chroma path.

### F-14 - Malformed Export Manifest Raises A Raw Traceback

- Type: Quality / CLI robustness
- Severity: Low
- Confidence: High
- Files:
  - `pdf_audiobook.py`
- Code references:
  - `pdf_audiobook.py:1176-1180`
  - `pdf_audiobook.py:1260-1320`
- Impact:
  - Invalid `manifest.json` content can raise `JSONDecodeError` instead of a controlled `PDFToAudiobookError`, resulting in a traceback rather than operator-friendly output.
- Remediation:
  - Convert JSON parse failures into a domain-specific playback error.

## Code Quality Inventory

These items are low severity by default unless they hide a specific bug.

### Repository-Wide Hygiene

- 149 Ruff findings across 30 files
- Highest-frequency categories:
  - 107 line-length violations
  - 17 import-order violations
  - 17 unused imports
- Notable correctness-adjacent items:
  - `main.py`: multiple unused imports suggest stale compatibility shims or dead re-export scaffolding
  - `audio_handler.py:537`: unused variable `best_start_index`
  - `memory_manager.py` and `tests/test_memory_manager.py`: `zip()` calls without explicit `strict=...`
  - `scripts/memory_inspect.py`: module imports not at top of file

### Recommended Handling

- Phase these as cleanup work after the High/Medium items.
- Fix correctness-adjacent warnings first:
  - unused variables
  - ambiguous `zip()` semantics
  - stale imports in control-path modules
- Batch the remaining formatting and line-length cleanup in one dedicated refactor PR to avoid noisy functional diffs.

## Mitigation Roadmap

### Phase 0 - Immediate Triage, 0-2 Days

- Owners:
  - Desktop owner
  - Backend owner
  - Security/release owner
- Goals:
  - fix F-01 loopback port hijack risk
  - disable query-string WebSocket tokens from the client
  - document temporary operational mitigations
- Dependencies:
  - F-03 depends on F-01 design choice because the handshake model may change the connection bootstrap flow

### Phase 1 - Data Integrity And Availability, 2-5 Days

- Owners:
  - Backend owner
  - Runtime owner
- Goals:
  - fix F-02 corrupt-settings overwrite
  - fix F-04 export path traversal
  - add queue bounds for F-05
  - cap and retain PDF jobs for F-06
  - add subprocess timeouts for F-07
- Dependencies:
  - F-06 and F-07 can be implemented independently
  - F-04 should land before any user-facing export playback enhancements

### Phase 2 - Configuration And Memory Correctness, 5-10 Days

- Owners:
  - Runtime owner
  - Memory owner
- Goals:
  - unify config source resolution for F-08
  - redesign duplicate detection and revision handling for F-09
  - improve diagnostics fidelity for F-13

### Phase 3 - Supply Chain And Quality Cleanup, 1-2 Weeks

- Owners:
  - Release/security owner
  - Runtime owner
- Goals:
  - triage and upgrade F-11 dependency exposure
  - pin model revisions for F-10
  - fix F-12 and F-14
  - batch low-severity Ruff cleanup

## Fix-Specific Testing Protocol

### Required Test Layers

- Unit tests:
  - path normalization and traversal rejection
  - config corruption behavior
  - bounded queue overflow handling
  - config source precedence
  - duplicate-memory conflict handling
  - subprocess timeout conversion
- Integration tests:
  - desktop-backend startup handshake against a fake loopback listener
  - authenticated WebSocket upgrade without query-token fallback
  - PDF job queue concurrency caps
  - end-to-end playback rejection for hostile export manifests
- Regression tests:
  - preserve previous config keys on partial updates
  - prevent memory-loss regressions on conflicting facts
  - verify bounded event streaming under long response generation
  - ensure timeout recovery does not wedge later turns or shutdown

## Long-Term Prevention

### CI/CD

- Add Semgrep, Ruff, Bandit, and pip-audit to CI on pull requests
- Upload SARIF or JSON artifacts for searchable retention
- Fail PRs on:
  - new High security findings
  - new dependency advisories without triage
  - reintroduction of query-string auth tokens
  - path traversal tests or bounded-queue tests failing

### Code Review Checklist Updates

- Reject URL-based secret transport when headers or secure channels are available
- Require ownership-preserving local IPC handshakes for privileged helper processes
- Require canonical path enforcement for any manifest-driven or user-provided file access
- Require bounded queues, worker caps, and retention limits on long-lived service endpoints
- Require explicit behavior for malformed persisted state instead of silent fallback-to-empty writes

### Developer Training

- Local IPC threat modeling on macOS loopback services
- Safe file-path normalization and traversal prevention
- Reliable subprocess management with timeouts and cancellation
- Supply-chain hardening for model downloads and dependency updates

## Post-Remediation Validation Checklist

- [ ] No High findings remain open
- [ ] WebSocket auth uses headers only
- [ ] Desktop startup verifies backend identity, not just port occupancy
- [ ] Settings updates preserve existing valid data and reject corrupt config state safely
- [ ] Export playback rejects out-of-root manifest paths
- [ ] Event streaming remains bounded under slow-consumer conditions
- [ ] PDF job execution is bounded and old job metadata expires
- [ ] External command hangs are converted into recoverable domain errors
- [ ] Explicit `config_path` is the actual source of truth for settings loading
- [ ] Memory updates preserve or version conflicting facts
- [ ] Dependency advisory status is documented and either patched or explicitly risk-accepted
- [ ] CI blocks regressions for the above controls

## Bottom Line

- Confirmed `Critical` findings: 0
- Confirmed `High` findings: 2
- Confirmed `Medium` findings: 9
- Confirmed `Low` findings: 3
- Large low-severity code-quality backlog: yes

The highest-risk problems are not generic lint issues. They are trust-boundary flaws in local IPC, persistent-settings corruption behavior, and several authenticated-but-real denial-of-service / path-handling issues in the helper service and PDF pipeline.
