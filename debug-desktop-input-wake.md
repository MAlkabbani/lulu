# Debug Session: desktop-input-wake
- **Status**: [OPEN]
- **Issue**: Desktop composer does not accept typing and wake mode does not reliably recognize `hey lulu`.
- **Debug Server**: http://127.0.0.1:7777/event
- **Log File**: .dbg/trae-debug-log-desktop-input-wake.ndjson

## Reproduction Steps
1. Build and launch the macOS app from `macos_app`.
2. Open the `Assistant` tab.
3. Click into `Send A Text Turn` and try typing.
4. Start continuous voice mode and say `hey lulu`.
5. Refresh diagnostics and capture the resulting debug log.

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | Composer never gains stable focus in the running app | High | Low | Pending |
| B | Composer state changes are being dropped or reset before text is visible | High | Low | Pending |
| C | Wake scan is losing speech before or during capture/VAD | High | Medium | Pending |
| D | Wake acoustic analysis is rejecting real utterances before transcript matching | Medium | Low | Pending |
| E | Wake transcription or transcript matching still rejects usable `hey lulu` variants | Medium | Medium | Pending |

## Instrumentation
- `macos_app/Sources/LuluApp/Features/Assistant/AssistantView.swift`
  - focus change
  - composer tap
  - text change
  - send button tap
- `app_core/runtime_controller.py`
  - wake capture result
  - wake acoustic analysis
  - wake transcription result
  - wake match result

## Log Evidence
- Hypothesis A: confirmed by focus-change events without any matching composer text-change events in the same runs, showing the assistant tab loads and focus flips but the composer control still does not mutate bound state.
- Hypothesis B: rejected as the primary failure mode because there was no evidence of transient text mutations being reset; the state never changed in the first place.
- Hypothesis C: partially confirmed. Wake capture succeeds intermittently, but many scans return `audio_is_none=true`, so the passive loop is often waiting through silence rather than missing a later stage.
- Hypothesis D: rejected as the sole blocker. Practical voice mode already keeps STT wake scans enabled even when the acoustic stage rejects, so the decisive failures move downstream.
- Hypothesis E: confirmed. The debug log captured Whisper outputs like `Hey, hello, what's up?`, `He looks up`, `Hello, what's up?`, `Hey, you're doing what I'm doing.`, and `e`, and the matcher rejected them with `below-threshold` or `too-short`.

## Verification Conclusion
- The text-mode failure is in the desktop composer implementation, so the fix changed the assistant composer to an explicit AppKit-backed `NSTextField` wrapper with direct binding and first-responder management.
- The wake-mode failure is primarily transcript quality and normalization, so the fix now:
  - passes `initial_prompt=settings.wake_phrase` into wake-scan Whisper calls;
  - accepts the repo-observed Whisper confusions `hey hello ...` and `he looks ...` as wake-phrase variants.
- Validation after the fix:
  - `PYTHONPATH=. .venv/bin/pytest tests/test_continuous_listening.py tests/test_runtime_controller.py`
  - `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun --sdk macosx swiftc -typecheck $(rg --files macos_app/Sources/LuluApp -g '*.swift') -target arm64-apple-macos14.0`
