# C1 Wake-Word / Continuous Listening Plan

## Summary

Implement C1 as an always-on voice mode that keeps Lulu in passive local listening, wakes on the fixed phrase `hey lulu`, opens a short follow-up conversation window, and then returns to passive listening automatically.

The design stays within the current local Apple Silicon architecture:

- keep `sounddevice` + `numpy` as the microphone/VAD foundation
- keep `mlx-whisper` as the only transcription path
- keep Ollama, Chroma, and the current router unchanged unless required by the listening orchestration
- keep macOS `say` and the new B1 streamed TTS path

Locked product behavior for C1:

- wake phrase is fixed to `hey lulu`
- wake detection uses a light transcript gate, not a separate wake-word model
- voice mode becomes always-on continuous listening by default
- after wake, Lulu stays available for a short conversation window of 12 seconds
- self-trigger prevention uses software cooldown during and after Lulu speech

Important sequencing note:

- the repo currently has uncommitted B1 changes in the worktree
- commit/push is outside this plan-mode turn and should happen before or alongside C1 execution, but is not performed in this plan

## Current State Analysis

### Current Runtime Loop

Based on `main.py`:

- Lulu currently runs a turn-based loop
- in voice mode it waits for a full VAD capture, transcribes it, routes it, then streams the final response into chunked TTS
- there is no passive listening state distinct from active turn capture
- there is no wake gate, follow-up window, or cooldown scheduler

### Current Audio Layer

Based on `audio_handler.py`:

- `record_until_silence()` captures a full speech segment once the mic is opened
- VAD logic already has chunk-based RMS detection and pre-roll buffering
- there is no low-cost passive scan mode for short wake checks
- there is no notion of conversation-window capture versus passive wake capture
- `MacOSTTS` now has a speech queue, but it does not expose speech-state flags or cooldown hooks

### Current Settings

Based on `config.py`:

- settings exist for VAD thresholds, chunk size, and max record time
- there are no settings yet for:
  - wake phrase
  - passive scan window
  - follow-up conversation timeout
  - TTS cooldown duration
  - continuous-listening mode toggle

### Current UI / Observability

Based on `terminal_ui.py` and `README.md`:

- the dashboard already exposes mode, transcript, streamed response, chunk counts, recent events, and latency metrics
- it does not yet distinguish:
  - passive listening
  - wake detection
  - armed conversation window
  - cooldown due to Lulu speech
- this observability layer is the correct place to show continuous-listening state transitions

### Current Tests And Repo State

Based on `tests/` and `git status --short`:

- there is no test coverage yet for wake detection or continuous listening orchestration
- the worktree currently contains uncommitted B1 files:
  - `README.md`
  - `audio_handler.py`
  - `config.py`
  - `llm_router.py`
  - `main.py`
  - `ollama_client.py`
  - `terminal_ui.py`
  - `tests/test_llm_router.py`
  - `tests/test_streaming_tts.py`
- there is no existing wake-word code in the repository

## Assumptions & Decisions

These decisions are locked from exploration plus user answers and should be treated as implementation requirements:

- voice mode is always continuous by default; no `--continuous` flag for C1
- wake phrase is fixed to `hey lulu`
- wake detection is transcript-gated:
  - passive mode captures short low-cost utterances
  - those utterances are transcribed with `mlx-whisper`
  - only transcripts beginning with or strongly matching `hey lulu` activate Lulu
- after wake, Lulu keeps a 12-second conversation window open for follow-up turns
- Lulu suppresses false retriggers using software cooldown while speaking and for a short period afterward
- do not add a dedicated wake-word engine in C1
- do not add interruption or barge-in cancellation in C1 unless required for correctness

Recommended interpretation of the short conversation window:

- the 12-second window resets after:
  - a successful wake
  - a completed user utterance inside the active window
  - a completed Lulu reply
- if no qualifying speech occurs before timeout, Lulu returns to passive wake listening

## Proposed Changes

### 1. `config.py`

What:

- add continuous-listening and wake-window settings

Why:

- C1 needs explicit runtime tuning for passive scan behavior and cooldown control

How:

- add:
  - `wake_phrase`
  - `wake_scan_max_record_seconds`
  - `wake_scan_min_speech_seconds`
  - `conversation_window_seconds`
  - `wake_cooldown_seconds`
  - `continuous_listening_enabled`
- keep voice mode defaulted to continuous listening, but allow config override if later needed
- keep values conservative for M1 latency and battery balance

### 2. `audio_handler.py`

What:

- add separate audio capture paths for passive wake scanning and active conversation turns

Why:

- passive listening and active conversation turns have different latency and capture goals

How:

- preserve the existing full-turn capture behavior for active conversation
- introduce a lighter passive scan capture path, for example:
  - shorter max recording window
  - shorter minimum speech requirement
  - same VAD foundation and pre-roll logic
- factor `record_until_silence()` so shared VAD logic can support both modes without copy-paste
- add a small helper for wake-phrase transcript matching:
  - normalize transcript
  - accept exact start like `hey lulu ...`
  - optionally accept bare `hey lulu`
  - reject normal non-wake utterances

Important boundary:

- C1 uses transcript-gated wake detection only
- do not add a second wake model or new audio dependency

### 3. `main.py`

What:

- replace the current simple voice turn loop with a stateful continuous-listening controller

Why:

- C1 is primarily an orchestration change across passive listening, wake activation, cooldown, conversation window, routing, and speech

How:

- keep the continuous and turn-based voice paths aligned under the same controller surface
- introduce explicit runtime states such as:
  - `passive_listening`
  - `wake_detected`
  - `conversation_window`
  - `thinking`
  - `streaming`
  - `speaking`
  - `cooldown`
- passive mode:
  - perform short wake-scan audio captures
  - if no speech, continue scanning
  - if speech exists, transcribe and test wake phrase
  - only enter active mode when wake phrase matches
- active conversation window:
  - once activated, capture normal user turns without requiring repeated wake phrase
  - reset the 12-second window after valid turns and replies
  - return to passive mode when the window expires
- cooldown:
  - while Lulu is speaking and briefly afterward, suppress wake scanning
  - use monotonic timing rather than sleep-heavy blocking if possible
- reuse the current B1 streamed reply path after an active utterance is accepted

Recommended top-level structure:

- extract the current “process one user turn” logic into a helper
- let the outer loop manage passive scan, wake detection, conversation window, and cooldown scheduling

### 4. `terminal_ui.py`

What:

- extend the UI to visualize continuous-listening state

Why:

- continuous listening becomes much harder to debug without explicit mode visibility

How:

- add UI support for:
  - passive scan mode
  - wake detection event
  - active conversation window countdown or status
  - cooldown status while/after TTS
- update the event log with clear lifecycle messages such as:
  - `Passive wake scan listening.`
  - `Wake phrase detected: hey lulu`
  - `Conversation window active: 12.0s remaining`
  - `Wake detection cooling down after speech.`
- keep the current compact layout; do not redesign the dashboard

### 5. `README.md`

What:

- document the new continuous-listening behavior and wake phrase flow

Why:

- the current README still describes pure turn-based voice mode

How:

- explain that voice mode is always-on by default
- document the passive `hey lulu` wake flow
- explain the 12-second follow-up window
- document that Lulu suppresses wake detection during and shortly after its own speech
- note that turn-based voice mode remains available for troubleshooting

### 6. Tests

Target files:

- keep existing tests
- add a new focused module such as `tests/test_continuous_listening.py`

What:

- add deterministic tests for wake detection and controller-state decisions

Why:

- C1 risk is mostly orchestration correctness, false retriggers, and conversation-window timing, not low-level STT math

How:

- extract pure helpers where possible so they can be tested without live audio devices
- cover at minimum:
  - wake-phrase normalization and matching
  - non-wake transcript rejection
  - conversation-window expiry behavior
  - conversation-window reset after reply
  - cooldown suppressing wake checks during/after speech
  - active-mode follow-up turn not requiring repeated `hey lulu`

Recommended approach:

- keep tests at the controller/helper level with fake clocks and fake audio/transcript sources
- avoid trying to fully integration-test the microphone device loop in automated tests

## Data Flow

### Passive Wake Cycle

1. Lulu enters passive listening mode
2. Short VAD-based scan capture runs
3. If no speech is detected, passive scan repeats
4. If short speech is detected, `mlx-whisper` transcribes it
5. If transcript matches `hey lulu`, Lulu activates the conversation window
6. If transcript does not match, passive mode resumes

### Active Conversation Cycle

1. Conversation window becomes active for 12 seconds
2. Lulu captures a normal user turn without requiring another wake phrase
3. Transcript goes through the existing router and B1 streamed reply path
4. When speech finishes, the conversation window resets
5. Additional follow-up turns are accepted until timeout
6. On timeout, Lulu returns to passive wake listening

### Cooldown Cycle

1. Lulu begins speaking
2. Wake detection is suspended while TTS is active
3. After TTS finishes, a short cooldown remains active
4. Once cooldown expires, passive wake scanning resumes if no conversation window is active

## Edge Cases And Failure Modes

- if passive scan captures speech that transcribes imperfectly, wake matching must be strict enough to avoid false positives
- if the wake phrase is detected with trailing content like `hey lulu what time is it`, the controller should route the remainder as the first active query when feasible, rather than forcing the user to repeat themselves
- if the wake phrase is detected alone, open the conversation window and wait for the next utterance
- if Lulu is still within cooldown, passive scans must not activate the wake path
- if the conversation window is active but the next capture has no transcript, keep the window alive only until timeout; do not reset it on silence alone
- if a follow-up utterance starts near the end of the window, prefer completing that utterance rather than cutting it off mid-capture
- if turn-based mode is used, continuous listening logic should stay bypassed

## Verification Steps

Implementation should be considered complete only if all of the following pass:

1. `pytest -q`
2. focused diagnostics are clean for:
   - `main.py`
   - `audio_handler.py`
   - `config.py`
   - `terminal_ui.py`
   - `README.md`
   - any new continuous-listening test module
3. manual runtime checks confirm:
   - passive voice mode waits for `hey lulu`
   - `hey lulu` alone opens the conversation window
   - `hey lulu what time is it` can activate and route naturally
   - follow-up turns work for up to 12 seconds without repeating the wake phrase
   - Lulu does not immediately retrigger on its own TTS output because cooldown is active
   - after timeout, Lulu returns to passive wake listening

## Out Of Scope For C1

- dedicated wake-word engines or separate keyword spotting models
- user-configurable wake phrases
- interruption / barge-in during Lulu speech
- echo cancellation or acoustic echo reference processing
- visual redesign of the dashboard
- major router, memory, or provider changes unrelated to continuous listening
