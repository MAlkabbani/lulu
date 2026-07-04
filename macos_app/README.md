# Lulu macOS App Preview

This directory contains the Stage 2 desktop-shell preview for Lulu VAIA.

## Scope

The current shell is intentionally thin and text-first:

- launches the local Python backend service
- performs health bootstrap and dependency reads
- starts the backend in text mode
- submits text turns over the local HTTP API
- listens to streamed runtime events over WebSocket
- surfaces Assistant, Diagnostics, and Settings views in SwiftUI

The shell now also includes an early voice-mode preview surface:

- start text, continuous voice, and turn-based voice runtime modes
- display wake guidance, wake score, cooldown, and conversation-window badges
- map wake, latency, memory, and TTS runtime events into native diagnostics panels
- preflight microphone authorization from the native shell before starting voice modes
- refresh a full runtime diagnostics snapshot from the backend service for parity checks

The desktop launcher prefers `127.0.0.1:8765` for local development, but if that port is already occupied by an older helper process it will choose a free loopback port for the new session instead of failing bootstrap.

Full microphone permission ownership, packaged app entitlements, PDF workflow UI, and signing remain later stages.

## Structure

- `Package.swift`: Swift Package manifest for source validation and Xcode opening
- `Sources/LuluApp/App/`: app entrypoint, content layout, and shared app model
- `Sources/LuluApp/Features/`: Assistant, Diagnostics, and Settings views
- `Sources/LuluApp/Models/`: Swift API and event models
- `Sources/LuluApp/Services/`: backend launch and HTTP/WebSocket integration

## Local Validation

Type-check the shell from this directory:

```bash
swiftc -typecheck Sources/LuluApp/App/*.swift \
  Sources/LuluApp/Features/Assistant/*.swift \
  Sources/LuluApp/Features/Diagnostics/*.swift \
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
