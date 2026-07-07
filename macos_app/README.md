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

Treat Stages 3 and 4 as complete for the current preview baseline. Stage 5 packaging and release hardening is now in progress:

- preview mode remains the supported repo-checkout workflow and expects a repo-local `.venv`
- packaged mode now has a real `Lulu.xcodeproj` app target plus a packaging script that assembles a bundled backend runtime
- both launch modes must preserve the same nonce-validated startup contract and header-only bearer auth
- packaged mode resolves writable state through App Support and Caches instead of the repo checkout
- release signing, notarization, and final distribution credentials still remain environment-driven maintainer steps
- clean-machine packaged installs must still handle optional PDF portable-export dependencies such as `ffmpeg`; the current repo-local installer already automates that dependency for checkout-based installs
- the desktop UI now surfaces packaged first-run recovery guidance for bundled backend, Ollama, required models, microphone access, and optional `ffmpeg`

### Packaging Status

Implemented now:

- repo-checkout preview mode
- real Xcode app project at `Lulu.xcodeproj` that reuses `Sources/LuluApp/**`
- bundle metadata in `Info.plist` and packaging entitlements in `Lulu.entitlements`
- backend helper bootstrap with nonce-validated startup
- packaged-mode path handling through app-support-backed state plus cache-root wiring
- packaged packaging scripts under `Packaging/` for app build, backend bundle assembly, and notarization handoff
- preserved header-only bearer auth and loopback trust boundary across launch modes

Planned later:

- maintainer-managed signing identity configuration
- notarized release credential setup and staple verification
- release distribution polish and clean-machine final signoff
- clean-machine handling for optional packaged dependency workflows such as portable PDF export support

## Structure

- `Package.swift`: Swift Package manifest for source validation and Xcode opening
- `Lulu.xcodeproj`: packaged app project that points at the existing Swift source tree
- `Info.plist`: bundle metadata, including microphone usage copy
- `Lulu.entitlements`: packaging entitlements file with App Sandbox left disabled for the initial direct-download release path
- `Packaging/`: scripts for backend bundle assembly, app packaging, and notarization handoff
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

Validate the packaged app project from this directory:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project Lulu.xcodeproj -list
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild \
  -project Lulu.xcodeproj \
  -scheme Lulu \
  -configuration Debug \
  -destination "platform=macOS" \
  CODE_SIGNING_ALLOWED=NO \
  build
```

Validate the release-packaging flow without signing or DMG creation:

```bash
LULU_SKIP_SIGNING=1 LULU_SKIP_DMG=1 ./Packaging/package_macos_app.sh
```

## Full Xcode Workflow

If full Xcode is installed but `xcodebuild` still reports Command Line Tools, do not switch global developer settings unless you want to. Use `DEVELOPER_DIR` for this repo-local workflow instead:

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -version
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -list
```

From `macos_app/`, Xcode should now recognize both:

- the Swift package for source-first iteration through `Package.swift`
- the `Lulu.xcodeproj` app target for packaged build work

### What “structured so it can be opened and continued in Xcode later” means

The current desktop shell is intentionally organized so the package and the packaged app target can share the same code:

- `Package.swift` gives Xcode a first-class entrypoint today
- `Lulu.xcodeproj` now provides the concrete packaged-app target for Stage 5 build automation
- `Sources/LuluApp/App/` already isolates the app lifecycle and shared state
- `Sources/LuluApp/Features/` isolates UI surfaces so they can move into a future `.xcodeproj` or workspace without code churn
- `Sources/LuluApp/Services/` keeps backend launch, HTTP, and WebSocket integration outside the SwiftUI views
- `Sources/LuluApp/Models/` keeps API and event contracts centralized so the shell can evolve without scattering decode logic through the UI

That means the packaging migration path stays low-risk:

1. open `Package.swift` directly in Xcode for preview builds and iteration
2. keep `Lulu.xcodeproj` pointed at the same Swift folders rather than duplicating app code
3. assemble the bundled backend under `Contents/Resources/backend` during packaging
4. add signing, notarization, and release credentials in packaging automation rather than rewriting the Swift code structure

### Current validation result

In this environment:

- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -version` succeeds
- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project Lulu.xcodeproj -list` resolves the `Lulu` scheme
- `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild -project Lulu.xcodeproj -scheme Lulu ... build` succeeds with `CODE_SIGNING_ALLOWED=NO`
- `LULU_SKIP_SIGNING=1 LULU_SKIP_DMG=1 ./Packaging/package_macos_app.sh` succeeds and assembles `Contents/Resources/backend`

Swift package dependency resolution may still hit environment-specific sandbox limits in some agent sessions, but the packaged app target itself is now concrete and buildable on a normal macOS Xcode machine.
