# Lulu macOS App Preview

This directory contains the current desktop-shell preview for Lulu VAIA.

## Scope

The current shell is intentionally thin and voice-first:

- launches the local Python backend service
- performs health bootstrap and dependency reads
- listens to streamed runtime events over WebSocket
- surfaces Assistant, Diagnostics, and Settings views in SwiftUI

The shell now also includes an early voice-mode preview surface:

- start continuous voice and turn-based voice runtime modes
- show a setup checklist for backend, microphone, audio-input, TTS, and optional PDF-export readiness
- display wake guidance, wake score, cooldown, and conversation-window badges
- map wake, latency, memory, and TTS runtime events into native diagnostics panels
- keep advanced wake metrics available behind progressive disclosure and filter runtime/event views
- preflight microphone authorization from the native shell before starting voice modes
- refresh a full runtime diagnostics snapshot from the backend service for parity checks

The shell also now includes an initial `PDF Audiobooks` utility surface:

- choose a source PDF and export root from native macOS pickers
- run backend-backed dry runs or full audiobook exports
- poll job status and show manifest/output paths plus progress lines
- reveal the output folder in Finder and copy output or manifest paths after a job completes
- keep PDF workflow state separate from the live assistant runtime UI

The desktop launcher now starts the backend helper on port `0`, waits for a nonce-validated startup record from the child process, and only then trusts the negotiated loopback port for authenticated HTTP and WebSocket traffic.

Treat Stages 3 and 4 as complete for the current preview baseline. Stage 5 packaging-readiness is now in progress:

- preview mode remains the supported repo-checkout workflow and expects a repo-local `.venv`
- packaged mode is being prepared as a distinct bootstrap path with app-support-backed runtime state
- both launch modes must preserve the same nonce-validated startup contract and header-only bearer auth
- full packaged-app entitlements, release signing, notarization, and distribution still remain later release-stage work
- clean-machine packaged installs must still handle optional PDF portable-export dependencies such as `ffmpeg`; the current repo-local installer already automates that dependency for checkout-based installs
- the desktop Settings view now surfaces launch-mode guidance so first-run expectations stay explicit while packaged onboarding is still being completed

### Packaging Status

Implemented now:

- repo-checkout preview mode
- backend helper bootstrap with nonce-validated startup
- packaged-mode path groundwork through app-support-backed runtime state
- preserved header-only bearer auth and loopback trust boundary across launch modes

Planned later:

- signed app bundle production
- packaged-app entitlements closeout
- notarization
- release packaging and distribution artifacts
- clean-machine handling for optional packaged dependency workflows such as portable PDF export support

## Structure

- `Package.swift`: Swift Package manifest for source validation and Xcode opening
- `Sources/LuluApp/App/`: app entrypoint, content layout, and shared app model
- `Sources/LuluApp/Features/`: Assistant, Diagnostics, Settings, and PDF Audiobooks views
- `Sources/LuluApp/Models/`: Swift API and event models
- `Sources/LuluApp/Services/`: backend launch and HTTP/WebSocket integration

## Local Validation

Type-check the shell from this directory:

```bash
swiftc -typecheck Sources/LuluApp/App/*.swift \
  Sources/LuluApp/Features/Assistant/*.swift \
  Sources/LuluApp/Features/Diagnostics/*.swift \
  Sources/LuluApp/Features/PDFAudiobooks/*.swift \
  Sources/LuluApp/Features/Settings/*.swift \
  Sources/LuluApp/Models/*.swift \
  Sources/LuluApp/Services/*.swift
```

`swift build` may still fail in constrained environments that only expose Command Line Tools with sandbox restrictions. Open `Package.swift` in full Xcode on a macOS development machine for the normal desktop-app workflow.

## Full Xcode Workflow

If full Xcode is installed but `xcodebuild` still reports Command Line Tools, do not switch global developer settings unless you want to. Use `DEVELOPER_DIR` for this repo-local workflow instead:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -version
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -list
```

From `macos_app/`, Xcode should recognize the Swift package and expose the `LuluApp` scheme.

### What “structured so it can be opened and continued in Xcode later” means

The current desktop shell is intentionally organized like a future app target even though it is still source-first:

- `Package.swift` gives Xcode a first-class entrypoint today
- `Sources/LuluApp/App/` already isolates the app lifecycle and shared state
- `Sources/LuluApp/Features/` isolates UI surfaces so they can move into a future `.xcodeproj` or workspace without code churn
- `Sources/LuluApp/Services/` keeps backend launch, HTTP, and WebSocket integration outside the SwiftUI views
- `Sources/LuluApp/Models/` keeps API and event contracts centralized so the shell can evolve without scattering decode logic through the UI

That means the near-future migration path is low-risk:

1. open `Package.swift` directly in Xcode for preview builds and iteration
2. continue expanding the shell while it remains an SPM-managed app source tree
3. when packaging work begins, create a dedicated Xcode app project or workspace that reuses these same folders with minimal relocation
4. add app entitlements, bundle metadata, assets, signing, and packaging there rather than rewriting the Swift code structure

### Current validation result

In this environment:

- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -version` succeeds
- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -list` resolves the `LuluApp` scheme
- `xcodebuild ... build` is still blocked by the sandboxed execution environment used by the coding agent during Swift package dependency resolution

That last issue is environmental, not a structural problem with the Swift package layout. On a normal local Xcode session outside the agent sandbox, this layout is the intended path forward until the packaging stage.
