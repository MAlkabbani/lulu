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
