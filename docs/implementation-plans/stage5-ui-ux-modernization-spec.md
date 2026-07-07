# Stage 5 UI And UX Modernization Spec

## Status

- Future implementation candidate
- Intended to guide the UI and UX modernization work that should accompany Stage 5 desktop packaging-readiness
- Not a stack redesign and not a product-scope expansion beyond the current local-first assistant and separate PDF workflow

## Summary

This spec turns the July 2026 UI/UX audit into a concrete implementation plan for Lulu VAIA.

The goal is to improve the quality of the current user-facing experience across:

- the macOS desktop shell
- the terminal dashboard
- the install and startup flows
- the PDF audiobook workflow
- the repo-facing user documentation

The recommended path is:

- preserve the current local-first architecture and security boundaries
- standardize user-facing terminology before redesigning surfaces
- modernize shared UI patterns and interaction states incrementally
- improve error prevention, recovery guidance, and accessibility
- keep all documentation and product copy factually accurate and aligned with shipped behavior
- use Stage 5 packaging work to reinforce, not fragment, the user experience

## Why This Exists

The current repository already provides real product value:

- the desktop shell is functional and backend-driven
- the PDF audiobook workflow is genuinely usable
- the terminal dashboard exposes strong observability
- the documentation is mostly accurate and honest about scope

However, the current experience still shows signs of rapid feature layering:

- status language is inconsistent across surfaces
- some UI states expose implementation details instead of task-focused guidance
- desktop accessibility work is largely absent
- diagnostics density is high and prioritization is weak
- terminal and CLI messaging use different tones and patterns
- repo docs are mostly aligned, but still contain terminology drift and a few stale references

This spec focuses on closing those gaps without weakening:

- local-only execution
- Python-first backend ownership
- loopback and token-protected desktop/backend IPC
- the separate PDF audiobook workflow boundary
- observability and operator visibility

## Locked Constraints And Invariants

These are implementation requirements unless a later decision doc explicitly supersedes them:

- macOS on Apple Silicon remains the desktop target
- the backend remains Python-first and backend-owned
- the SwiftUI app remains a thin client over the existing local API and WebSocket surfaces
- no UI modernization work may move privileged logic into the GUI layer for convenience
- the PDF audiobook flow remains a distinct utility workflow, not part of the live assistant transcript surface
- voice runtime remains voice-only; typed text-turn support must not be reintroduced implicitly
- Stage 5 packaging work must preserve the current nonce-validated startup handshake and header-only bearer auth model
- documentation must remain truthful about shipped behavior, preview constraints, and deferred work
- accessibility, wording accuracy, and recovery guidance are first-class concerns, not later polish

## Current-State Findings

### Strong Baseline

- the macOS app is split cleanly by feature under `macos_app/Sources/LuluApp/Features/`
- the desktop shell already uses backend-owned state through `AppModel.swift`
- the PDF workflow is intentionally isolated from assistant runtime UX
- install, startup, and PDF CLI surfaces already favor actionable failures and explicit constraints
- docs broadly align with the current Stage 4 complete / Stage 5 next state

### Primary Friction

- status, empty-state, and dependency wording vary across tabs and docs
- the desktop shell duplicates Settings navigation in both the tab bar and the dedicated Settings scene
- several PDF and settings inputs rely on placeholder-only field labeling
- blocked actions often fail after click instead of being explained before click
- diagnostics expose too much equal-weight detail without overview-first prioritization
- terminal dashboards still leak raw internal state values and mix assistant output with system notices
- there is no centralized user-facing copy or semantic status mapping layer
- there are no explicit accessibility annotations in the SwiftUI layer

## Modernization Goals

### Product Goals

- make the current desktop shell feel coherent, predictable, and easier to recover from
- keep the PDF workflow reliable while making it less utility-like and more task-guided
- preserve rich diagnostics while separating primary UX from advanced operator detail
- ensure every user-facing surface uses consistent, accurate, and approved wording
- use the same UX rules across desktop UI, terminal UI, CLI wrappers, and docs

### Non-Goals

- redesigning the product into a consumer chat app
- replacing the backend stack, TTS engine, or local provider model in this spec
- broad visual rebranding or speculative theming work
- hiding operator-grade observability behind a simplified interface
- adding OCR, barge-in, cross-platform clients, or cloud features

## Success Metrics

- `Consistency`: all user-facing status labels and empty states come from a shared approved vocabulary
- `Accuracy`: no stale claims remain about shipped stages, packaged behavior, or optional versus required dependencies
- `Prevention`: blocked actions explain why they are blocked before the user clicks them
- `Recovery`: every significant warning or failure state includes the next useful user action when one exists
- `Accessibility`: all critical interactive controls are keyboard reachable, visibly labeled, and VoiceOver-friendly
- `Usability`: five critical workflows can be completed cleanly in guided testing:
  - first desktop launch
  - microphone permission recovery
  - voice runtime start and stop
  - PDF dry run
  - PDF export with and without portable conversion
- `Terminal Clarity`: internal state names no longer appear raw in operator-facing mode and path badges
- `Docs Quality`: README and runbook workflows are accurate enough for a clean-reader walkthrough without clarification

## Shared UX Rules

These rules should guide all implementation slices:

- use one approved term per concept across desktop, terminal, CLI, and docs
- distinguish clearly between `not configured`, `not available`, `unavailable`, `no data yet`, and `failed`
- never show raw snake_case or internal backend state labels to end users
- keep assistant responses separate from runtime, dependency, or troubleshooting notices
- use visible field labels for important inputs; placeholders may supplement, but must not be the only label
- disable impossible actions proactively and explain the reason inline
- do not rely on color alone to convey state
- prefer task-focused guidance over implementation-focused explanation
- preserve factual honesty about limitations, especially preview-mode and packaged-mode boundaries

## Proposed Shared Vocabulary

These terms should be normalized across the repo unless a specific surface needs a tighter variant:

- backend health: `Ready` / `Unavailable`
- dependency availability: `Available` / `Unavailable`
- optional dependency gap: `Optional dependency unavailable`
- empty state for absent data: `No data yet`
- empty state for no prior action: `No activity yet`
- settings load gap: `Not loaded yet`
- PDF workflow mode: `Dry Run` / `Export`
- PDF workflow state: `Pending` / `Running` / `Completed` / `Failed`
- assistant runtime states should be mapped to human labels such as `Ready`, `Listening`, `Thinking`, `Speaking`, `Cooldown`, and `Error`

## Implementation Slices

### UX1: Shared Content And Status Semantics

Goal:

- create one semantic source of truth for user-facing status language, empty states, and recovery copy

Why:

- this is the highest-leverage slice because every other modernization step depends on consistent content

Target files:

- `macos_app/Sources/LuluApp/App/AppModel.swift`
- `macos_app/Sources/LuluApp/Models/BackendModels.swift`
- `macos_app/Sources/LuluApp/Features/Assistant/AssistantView.swift`
- `macos_app/Sources/LuluApp/Features/Diagnostics/DiagnosticsView.swift`
- `macos_app/Sources/LuluApp/Features/PDFAudiobooks/PDFAudiobooksView.swift`
- `macos_app/Sources/LuluApp/Features/Settings/SettingsView.swift`
- `terminal_ui.py`
- `app_core/runtime_controller.py`
- `docs/README.md`
- `README.md`
- `docs/operations.md`
- `docs/pdf-audiobooks.md`

Implementation steps:

- introduce shared semantic mapping helpers for desktop status labels and empty-state strings
- map raw backend runtime modes to human-readable UI labels before rendering
- normalize dependency, PDF job, and settings states to a consistent vocabulary
- remove direct literal duplication where the same concept appears in multiple UI surfaces
- update docs to use the same approved terminology dictionary

Acceptance criteria:

- no user-facing desktop or terminal view renders raw snake_case mode names
- the same concept is labeled consistently across Assistant, Diagnostics, PDF, terminal, and docs
- empty-state language follows the approved vocabulary
- documentation examples and headings use the same product terms as the app

### UX2: Desktop Navigation And Shared Component Foundation

Goal:

- simplify navigation and create reusable desktop UI building blocks

Why:

- the current app has strong feature boundaries but repeated ad hoc UI primitives and duplicated Settings entry points

Target files:

- `macos_app/Sources/LuluApp/App/ContentView.swift`
- `macos_app/Sources/LuluApp/App/LuluDesktopApp.swift`
- new `macos_app/Sources/LuluApp/Features/Shared/` or equivalent shared UI folder
- `macos_app/Sources/LuluApp/Features/Assistant/AssistantView.swift`
- `macos_app/Sources/LuluApp/Features/Diagnostics/DiagnosticsView.swift`
- `macos_app/Sources/LuluApp/Features/PDFAudiobooks/PDFAudiobooksView.swift`

Implementation steps:

- choose a single preferences entry model and remove duplicate Settings navigation
- introduce reusable components such as:
  - `StatusBadge`
  - `InlineNotice`
  - `EmptyStateView`
  - `LabeledMetricRow`
  - `DisabledActionHint`
- replace repeated badge and metric rendering code with shared components
- keep visuals simple and native; prefer consistency and clarity over custom styling

Acceptance criteria:

- Settings appears in only one primary navigation path
- at least the Assistant, Diagnostics, and PDF surfaces use shared semantic components instead of duplicated badge helpers
- empty, warning, and informational states look and read consistently across tabs
- the app remains type-check clean after the shared component extraction

### UX3: Assistant And Diagnostics Experience Cleanup

Goal:

- make the primary assistant flow clearer and move advanced observability into better-prioritized diagnostics

Why:

- the assistant surface should feel actionable, while diagnostics should remain deep but easier to parse

Target files:

- `macos_app/Sources/LuluApp/Features/Assistant/AssistantView.swift`
- `macos_app/Sources/LuluApp/Features/Diagnostics/DiagnosticsView.swift`
- `macos_app/Sources/LuluApp/App/AppModel.swift`
- `macos_app/Sources/LuluApp/Models/BackendModels.swift`

Implementation steps:

- disable voice actions when prerequisites are unmet and show inline reasons before click
- elevate the most important assistant guidance near the action area
- separate backend/system notices from assistant response text where they currently blur together
- restructure Diagnostics into overview-first groups with lower-priority detail below
- add short explanations or help text for wake metrics and advanced terminology where needed

Acceptance criteria:

- users can tell at a glance whether they can start voice mode and why not, if blocked
- the assistant response area contains assistant output rather than mixed troubleshooting copy
- diagnostics present runtime health, dependencies, and next actions before raw event detail
- dense metrics remain available without dominating the first screenful

### UX4: PDF Workflow Modernization

Goal:

- improve the PDF desktop workflow so it is guided, accessible, and recovery-friendly without changing backend job semantics

Why:

- the workflow is already functional, but it still reads like a utility form rather than a polished product task flow

Target files:

- `macos_app/Sources/LuluApp/Features/PDFAudiobooks/PDFAudiobooksView.swift`
- `macos_app/Sources/LuluApp/App/AppModel.swift`
- `macos_app/Sources/LuluApp/Models/BackendModels.swift`
- `backend_service/api_models.py`
- `README.md`
- `docs/pdf-audiobooks.md`

Implementation steps:

- replace placeholder-only PDF fields with visible labels and supporting help text
- add stronger file and export-destination guidance near the relevant controls
- show disabled-state reasons for blocked PDF actions
- clarify dry-run versus export versus portable conversion behavior inline
- add clear post-completion affordances such as copying or revealing output paths if they fit native app constraints
- keep ffmpeg messaging precise: optional for AIFF-only workflows, required for portable conversion

Acceptance criteria:

- users can understand the difference between dry run, export, and portable conversion without reading external docs
- missing input and dependency states are explained before the user submits a job
- job success and failure states clearly identify output location, manifest availability, and next action
- PDF wording matches the approved shared vocabulary in app and docs

### UX5: Terminal And CLI Message Normalization

Goal:

- make terminal and CLI UX as coherent and user-readable as the desktop shell

Why:

- Lulu still has a strong terminal-first operator story, so Stage 5 should improve that surface instead of leaving it behind

Target files:

- `terminal_ui.py`
- `main.py`
- `app_core/runtime_controller.py`
- `scripts/install_lulu.sh`
- `scripts/start_lulu.sh`
- `scripts/memory_inspect.py`
- `backend_service/service_runner.py`
- `pdf_audiobook.py`

Implementation steps:

- map raw mode and invocation identifiers to approved human-facing labels
- separate assistant output from runtime/system notices in the terminal dashboard
- normalize CLI tone across wrappers and utilities
- improve help output for direct CLI entrypoints that currently assume internal familiarity
- keep machine-readable startup records intact while ensuring human-facing failures stay understandable

Acceptance criteria:

- the terminal dashboard never exposes raw internal mode and invocation identifiers to normal users
- the response panel reflects assistant content only
- CLI tools use a coherent tone and error structure
- direct `--help` and failure text are actionable for contributors and operators

### UX6: Accessibility, Documentation Consistency, And Validation Gates

Goal:

- make clarity, accessibility, and factual accuracy enforceable rather than aspirational

Why:

- the repo needs repeatable validation so future feature work does not reintroduce drift

Target files:

- `macos_app/Sources/LuluApp/Features/Assistant/AssistantView.swift`
- `macos_app/Sources/LuluApp/Features/Diagnostics/DiagnosticsView.swift`
- `macos_app/Sources/LuluApp/Features/PDFAudiobooks/PDFAudiobooksView.swift`
- `macos_app/Sources/LuluApp/Features/Settings/SettingsView.swift`
- `README.md`
- `docs/README.md`
- `docs/operations.md`
- `docs/pdf-audiobooks.md`
- `docs/prd.md`
- `macos_app/README.md`
- focused desktop and Python test files as needed

Implementation steps:

- add SwiftUI accessibility labels, hints, and grouping for critical controls and state badges
- ensure color is not the only conveyor of status
- fix stale or drifted wording in README, runbook, PRD, and feature docs
- add copy and status mapping coverage where feasible through focused tests
- add a lightweight repo check for banned raw state labels or known stale phrasing patterns if the maintenance cost stays low

Acceptance criteria:

- key desktop workflows are usable with keyboard navigation and VoiceOver-friendly labeling
- docs no longer contain stale stage claims, numbering errors, or naming drift for core workflows
- focused tests or checks protect the shared vocabulary and state-mapping rules
- the repo has an explicit UX validation checklist for future changes

## Testing Protocol

### Focused Automated Validation

- `swiftc -typecheck` for all edited Swift sources
- focused Swift-side tests if the project pattern supports them
- focused `pytest` coverage for any backend or terminal mapping changes
- lightweight content checks for banned raw state strings and known stale wording patterns where useful

### Manual UX Validation

- first-launch desktop bootstrap
- backend-unavailable recovery
- microphone denied and microphone not-yet-requested states
- continuous and turn-based voice start/stop
- PDF dry run with valid text PDF
- PDF export with valid text PDF
- PDF export rejection for encrypted PDF
- PDF export rejection for image-only PDF
- portable export with `ffmpeg` unavailable
- terminal dashboard sanity pass during one full voice interaction turn

### Accessibility Validation

- keyboard-only walkthrough of the desktop shell
- VoiceOver review of primary controls, status surfaces, and PDF form fields
- visual review for color-only status meaning

### Documentation Validation

- clean-reader walkthrough using `README.md` and `docs/operations.md`
- PDF workflow walkthrough using only `docs/pdf-audiobooks.md`
- packaged-mode wording review to ensure future-state claims remain explicitly future-state

## Recommended Execution Order

1. `UX1` shared content and status semantics
2. `UX2` desktop navigation and shared component foundation
3. `UX3` assistant and diagnostics cleanup
4. `UX4` PDF workflow modernization
5. `UX5` terminal and CLI normalization
6. `UX6` accessibility, documentation consistency, and validation gates

This order is intentional:

- `UX1` prevents later wording churn
- `UX2` gives later slices reusable building blocks
- `UX3` and `UX4` improve the highest-value end-user surfaces first
- `UX5` keeps the terminal/operator story aligned
- `UX6` locks the improvements in place for Stage 5 and beyond

## Definition Of Done

This modernization spec can be considered complete only when:

- desktop, terminal, CLI, and docs use the approved shared vocabulary
- blocked actions explain themselves before the user clicks
- the desktop shell has explicit accessibility treatment for key workflows
- diagnostics remain deep but become overview-first and easier to scan
- PDF workflow guidance is clearer without weakening backend validation
- docs are accurate, current, and aligned with shipped behavior
- focused validation confirms the UX improvements do not regress usability, accessibility, or factual accuracy
