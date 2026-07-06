# B1 Chunked TTS Streaming Plan

## Summary

Implement B1 by adding phrase-boundary chunked TTS streaming on top of Lulu's current observability layer.

The design goal is to reduce perceived response latency without changing the current local-first stack:

- keep Ollama as the local LLM runtime
- keep macOS native `say` as the MVP TTS engine
- keep the current VAD and STT pipeline
- build on the new terminal UI so streamed generation and spoken chunks are visible in real time

The chosen product behavior for B1 is:

- chunking uses phrase boundaries rather than whole-sentence boundaries
- all assistant replies use streaming playback, including short fixed acknowledgements
- playback is non-interruptible in B1
- interruption and cutover remain explicitly deferred to C1 wake-word / continuous listening

Useful future comment to preserve in the implementation:

- phrase-boundary chunking improves perceived latency now, but may sound choppier than sentence-boundary chunking; leave clear comments around the chunking policy so it can be swapped later if user testing prefers smoother speech

## Current State Analysis

### Current Main Loop

Based on `main.py`:

- the app currently captures audio input, then waits for `router.handle_transcript(...)` to return a full `RouteResult`
- TTS only begins after the full response exists
- timing is already tracked in the terminal UI for `capture`, `stt`, `router`, `tts`, and `total`
- the observability layer is strong enough to surface streaming state without architectural redesign

### Current TTS

Based on `audio_handler.py`:

- `MacOSTTS.speak(text)` is a single blocking `subprocess.run(["say", clean_text])`
- there is no queue, worker, chunking, or partial playback path
- there is no cancellation or interrupt mechanism

### Current LLM API Capability

Based on `ollama_client.py`:

- `chat(...)` is non-streaming and returns a full message
- `stream_chat(...)` already exists and yields content chunks from Ollama `/api/chat`
- there is no higher-level streaming orchestration for tool-call scenarios

### Current Router Shape

Based on `llm_router.py`:

- explicit `insert info ...` bypasses the normal chat flow and returns a fixed acknowledgement string
- normal chat first performs a non-streaming assistant call with tools enabled
- if no tool is requested, the router returns the full assistant text immediately
- if a tool is requested, the router executes the tool, sends the tool result back, and then makes a second non-streaming final chat call
- this means B1 cannot simply switch one line to `stream_chat(...)`; it needs a two-phase streaming design:
  - phase 1: tool detection remains non-streaming
  - phase 2: final assistant text generation becomes streaming

### Current Observability Layer

Based on `terminal_ui.py`:

- the dashboard already renders status, latencies, transcript, response, recent saves, and recent turn events
- it does not yet distinguish between:
  - streamed partial model text
  - chunk queue status
  - spoken chunk completion
- it is the correct place to show chunk-level generation and playback progress

## Assumptions & Decisions

These are locked from repo exploration plus user answers:

- B1 uses phrase-boundary chunking, not sentence-only chunking
- B1 applies streaming to all assistant replies
- B1 playback is non-interruptible
- `say` remains the TTS engine for B1; do not introduce a new speech stack yet
- the current router tool contract remains unchanged
- explicit save acknowledgements still flow through the same assistant speech path, but they may stream as a single short chunk
- C1 wake-word / continuous listening is next and should not be partially implemented inside B1

Recommended architectural stance:

- use a dedicated TTS chunk queue plus a speech worker abstraction rather than trying to call `say` inline as streamed text arrives
- keep tool detection non-streaming and only stream the final assistant text after tool resolution, because that matches the current router’s two-stage behavior and avoids speculative chunk playback before the tool path is settled
- keep chunking policy isolated in one helper so phrase-boundary logic can later be replaced by sentence-boundary or punctuation-aware heuristics without touching the rest of the pipeline

## Proposed Changes

### 1. `config.py`

What:

- add settings for chunked speech behavior and buffer thresholds

Why:

- phrase-boundary chunking needs explicit knobs for buffering and future tuning

How:

- add `tts_stream_min_chunk_chars`
- add `tts_stream_soft_chunk_chars`
- add `tts_stream_max_chunk_chars`
- optionally add `tts_stream_pause_punctuation` only if implemented as a constant is not sufficient

Notes:

- defaults should favor low latency with reasonable phrase coherence
- avoid adding speculative interruption settings in B1

### 2. `audio_handler.py`

What:

- expand the TTS layer from one blocking `speak(text)` call into a chunk-capable speech interface

Why:

- B1 needs to queue and speak chunks progressively while the model is still generating text

How:

- keep `MacOSTTS` as the backing engine
- add a small internal speech queue / worker model, for example:
  - begin a turn
  - enqueue chunk
  - finish turn and wait for queue drain
- keep `say` invocation non-shell-interpolated
- preserve a simple `speak(text)` compatibility path where useful, but add chunk-oriented methods for B1

Expected behavior:

- chunks are spoken in order
- playback remains blocking from the user’s turn perspective, but generation-to-speech overlap reduces perceived latency
- no cancellation path is added in B1

Useful code comment to include:

- phrase-boundary chunking is intentionally chosen for lower latency and may be replaced later if user testing prefers smoother sentence-level speech

### 3. `ollama_client.py`

What:

- extend the streaming API so the caller can stream final assistant text with the same message payload shape already used by the router

Why:

- B1 needs a reliable streamed text source for final assistant generation

How:

- keep the existing `stream_chat(messages)` as the low-level primitive if it already matches the needed request shape
- optionally add a richer helper that returns streamed text chunks plus the final accumulated text if that makes `main.py` and `llm_router.py` cleaner
- do not redesign the native Ollama payload structure

### 4. `llm_router.py`

What:

- split routing into explicit phases so final assistant text can be streamed after tool resolution

Why:

- the current `handle_transcript(...)` API is full-response oriented and hides the tool-detection/final-generation boundary

How:

- preserve current behavior, but refactor into smaller steps such as:
  - build messages from transcript and recalled memory
  - run non-streaming initial assistant/tool-detection pass
  - execute tool if present
  - return the final message list needed for streaming the assistant’s final text
- introduce a B1-oriented API that exposes enough state for the caller to stream, such as:
  - explicit fixed reply path
  - non-tool final messages
  - tool-resolved final messages
  - recalled memory hits
  - saved items

Important constraint:

- for tool-calling turns, only the post-tool final assistant response is streamed
- do not stream speculative text from the first assistant pass, because it may be replaced by tool execution

### 5. `main.py`

What:

- change the turn execution path from full-response TTS to streamed final-response playback

Why:

- this is the orchestration point where UI, router, Ollama streaming, chunking, and speech come together

How:

- keep capture and transcription flow unchanged
- replace the current `result = router.handle_transcript(...)` plus `tts.speak(result.reply_text)` flow with:
  1. run routing/tool-detection phase
  2. update UI with memory hits and save outcomes
  3. start streamed final generation or fixed-reply chunk path
  4. buffer incoming text until a phrase boundary is reached
  5. enqueue each ready phrase chunk to the TTS worker
  6. update the UI response panel incrementally as text streams in
  7. finalize the turn once the stream and speech queue are drained

Latencies:

- keep existing latency metrics
- add at least one of:
  - `first_token`
  - `first_spoken_chunk`
  - `stream_total`
- ensure the existing dashboard remains meaningful during a streamed turn

### 6. `terminal_ui.py`

What:

- extend observability to represent streamed generation and chunk playback

Why:

- B1 is explicitly being built on top of the new observability layer

How:

- update UI state so it can show:
  - partial streamed response text
  - chunk playback milestones
  - first-spoken-chunk timing if captured
  - event log entries for:
    - streaming started
    - chunk emitted
    - chunk spoken
    - stream finished
- keep the layout compact and avoid introducing a new full-screen UI mode

Recommended event examples:

- `Streaming final response from Ollama.`
- `Emitted speech chunk: Thanks, I found your note,`
- `Spoke chunk 2 of 4.`
- `Finished streamed playback.`

### 7. `README.md`

What:

- update docs to reflect streamed phrase-boundary playback and the richer dashboard behavior

Why:

- the current README describes the dashboard and current response behavior, but not streamed chunk playback

How:

- document that replies now begin speaking before the full response is complete
- explain that chunking uses phrase boundaries for lower latency
- note that playback is non-interruptible in B1
- describe any new latency fields or event-log behavior visible in the dashboard

### 8. Tests

Target files:

- `tests/test_llm_router.py`
- add a new focused module such as `tests/test_streaming_tts.py`

What:

- add focused tests for chunking and routing-phase behavior

Why:

- the main risk in B1 is incorrect chunk boundary handling or incorrect router behavior in tool-calling turns

How:

- keep router tests focused on behavior contracts, not terminal rendering
- add deterministic tests for:
  - phrase-boundary chunk extraction
  - explicit fixed replies being routed through the chunk path
  - tool-calling turns only streaming after tool execution
  - final accumulated response text matching the streamed pieces
  - TTS queue order for multiple emitted chunks

## Data Flow

### Non-Tool Turn

1. User finishes speaking
2. STT produces transcript
3. Router builds context and messages
4. Initial assistant pass detects no tool call
5. Main loop starts streamed final generation from Ollama
6. Partial text accumulates in a chunk buffer
7. When a phrase boundary is reached, a speech chunk is emitted to the TTS queue
8. UI updates response text and event log during the stream
9. Remaining buffered text is flushed at stream end
10. Turn completes after the TTS queue drains

### Tool-Calling Turn

1. User finishes speaking
2. Router runs the initial non-streaming tool-detection pass
3. Tool executes and updates memory if needed
4. Final message list is prepared
5. Only the post-tool final assistant response is streamed
6. Phrase chunks are emitted and spoken in order
7. UI reflects save events plus streaming playback events

### Explicit Command Turn

1. User says `insert info ...`
2. Explicit save executes immediately
3. The acknowledgement reply is still sent through the chunked playback path
4. For short acknowledgements, this will usually result in one short spoken chunk

## Edge Cases And Failure Modes

- if the stream produces tiny fragments with no punctuation, the buffer must still flush once it crosses a soft or max size threshold
- if the final stream ends without punctuation, flush the remaining buffered text
- if a short acknowledgement never reaches a phrase boundary, speak it as one final chunk
- if `say` fails for one chunk, log the failure and continue draining remaining chunks only if that is clearly safe; otherwise fail the turn visibly
- if the router returns no final assistant reply, do not start streaming
- if a tool-calling first pass returns content plus a tool call, ignore speculative content and stream only the post-tool final reply
- because B1 is non-interruptible, new user input should not cancel active playback

## Verification Steps

Implementation should be considered complete only if all of the following pass:

1. `pytest -q`
2. focused diagnostics are clean for:
   - `main.py`
   - `audio_handler.py`
   - `ollama_client.py`
   - `llm_router.py`
   - `terminal_ui.py`
   - any new streaming test module
3. manual text-mode sanity checks confirm:
   - response text appears progressively in the dashboard
   - phrase chunks begin speaking before the full reply is complete
   - explicit save acknowledgements still speak correctly
   - tool-calling turns stream only the post-tool final response
   - event log reflects streaming and chunk playback milestones

## Out Of Scope For B1

- wake-word or continuous listening
- playback interruption or cancellation
- replacing macOS `say` with a different TTS engine
- wake-word-style barge-in or self-interruption handling
- major router/provider redesign
