# A1 Memory Deduplication And Categories Plan

## Summary

Implement A1 for Lulu by upgrading the current flat Chroma memory layer into a canonical memory store with:

- semantic deduplication before insert/update
- free-form backend-assigned tags limited to 1-3 tags per memory
- latest-wins conflict handling for overlapping facts
- tag-aware recall formatting so retrieved memories expose both text and tags to the LLM

The design keeps the current local-only architecture intact:

- Ollama remains the only local model runtime
- Chroma remains the persistent store in `./vault_db`
- explicit `insert info ...` and autonomous `save_to_memory(fact)` both continue to work
- no new cloud services, no CUDA, no stack redesign

## Current State Analysis

### Repository Shape

The current repository contains the core Lulu MVP files:

- `config.py`
- `memory_manager.py`
- `llm_router.py`
- `ollama_client.py`
- `main.py`
- `audio_handler.py`
- `tests/test_llm_router.py`
- `README.md`

### Current Memory Behavior

Based on `memory_manager.py`:

- all memories are stored as plain text documents in a single Chroma collection
- each insert always generates a new UUID
- metadata currently contains only `source`
- there is no category, tag, canonical identity, deduplication, or update path
- retrieval returns text, distance, and metadata, but recall formatting only shows `source`

### Current Router Behavior

Based on `llm_router.py`:

- the explicit `insert info ...` path directly calls `MemoryManager.upsert_memory(payload, source="explicit")`
- the autonomous path retrieves top-k memories, injects them into the system prompt, and exposes one tool: `save_to_memory(fact)`
- tool execution validates `fact`, then also calls `MemoryManager.upsert_memory(fact, source="tool_call")`
- the tool schema currently carries only `fact`, which aligns with the chosen backend-classification approach

### Current Settings And Tests

Based on `config.py` and `tests/test_llm_router.py`:

- there are no settings yet for deduplication thresholds, tag limits, or category-classification controls
- tests currently cover only the explicit save bypass and the single tool-call follow-up path
- there are no tests for updating existing memory records, tag assignment, or tag-aware recall formatting

## Assumptions & Decisions

These decisions are locked from exploration plus user answers and should be treated as implementation requirements:

- tags use free-form names, not a fixed taxonomy
- tags are assigned in the backend after the save request, not provided by the tool caller
- each memory stores 1-3 tags
- deduplication is primarily semantic, using embedding similarity
- exact or normalized text checks may be used as a secondary fallback, not the primary gate
- when a new memory conflicts with an older memory in the same semantic slot, latest wins
- retrieved memories should show both text and tags to the LLM
- A1 should ship with focused unit tests, not a broad integration harness
- the existing tool contract should stay simple where possible; avoid unneeded expansion of `save_to_memory`

Best-fit setup and rationale:

- keep one Chroma collection rather than splitting by category, because free-form tags would fragment storage and complicate recall without proven benefit at the current repo size
- perform deduplication and classification inside `MemoryManager`, because both explicit and autonomous save paths already converge there
- preserve the current tool schema with `fact` only, because backend classification avoids tool-schema churn and reduces prompt complexity
- enrich metadata aggressively, because recall, updates, and future UI/analytics features depend on this more than on structural schema changes

## Proposed Changes

### 1. `config.py`

What:

- add memory-tuning settings for deduplication and backend classification

Why:

- thresholds and limits should be configurable without code edits

How:

- add `memory_dedup_similarity_threshold`
- add `memory_dedup_query_k`
- add `memory_max_tags`
- optionally add `memory_tag_classifier_model`; default this to the existing chat model unless there is a strong reason to separate later
- keep defaults conservative and local-only

Expected new environment variable examples:

- `MEMORY_DEDUP_SIMILARITY_THRESHOLD`
- `MEMORY_DEDUP_QUERY_K`
- `MEMORY_MAX_TAGS`

### 2. `memory_manager.py`

What:

- expand the memory model and replace blind insert-only behavior with canonical save-or-update behavior

Why:

- this is the correct boundary for deduplication, classification, metadata consistency, and latest-wins updates

How:

- introduce richer metadata fields, at minimum:
  - `source`
  - `tags`
  - `normalized_text`
  - `created_at`
  - `updated_at`
  - `memory_kind` or equivalent canonical marker if helpful
- extend `MemoryHit` to expose tags and any metadata needed by recall formatting
- replace `upsert_memory()` internals with this flow:
  1. validate and normalize the incoming text
  2. embed the incoming text
  3. query the existing collection for top semantic candidates using the same embedding
  4. decide whether the best candidate crosses the dedup threshold
  5. if no strong match exists:
     - classify tags in the backend
     - insert a new canonical memory
  6. if a strong match exists:
     - treat the new text as the latest canonical version
     - merge or replace metadata
     - keep the existing Chroma id when possible
     - re-embed the updated text and update the record
- add small internal helper methods rather than one long save function:
  - normalize input text
  - classify tags
  - find duplicate candidate
  - build metadata payload
  - format recall context
- update `format_context()` so each retrieved memory line includes both tags and source, for example:
  - `1. [tags: preference, tea] (tool_call) My favorite tea is jasmine`

Important non-conflict rule:

- latest-wins must update the canonical record rather than keeping two active conflicting records for the same semantic slot
- do not implement revision history in A1, because the chosen scope is focused dedup plus categories, not audit history

### 3. `ollama_client.py`

What:

- add one backend-facing helper for tag classification using the local Ollama chat model

Why:

- user chose backend classification after save, so the backend needs a consistent local classifier path

How:

- add a helper method that asks the local chat model for 1-3 free-form tags for a fact
- require structured, low-variance output from the model
- keep this classification call internal to memory persistence, not exposed as a user tool
- validate the returned tags in Python:
  - dedupe tag strings
  - trim whitespace
  - lowercase or normalize consistently
  - cap to 1-3 tags
  - fall back to a safe tag such as `general` if parsing fails

Implementation guidance:

- prefer a small deterministic prompt and JSON-shaped response
- if structured output support is not added in A1, require a strict parseable format and validate aggressively
- do not broaden public user-facing interfaces just to support this internal classifier

### 4. `llm_router.py`

What:

- keep the current save tool simple, but align prompts and save flows with the new tag-aware memory layer

Why:

- backend classification was chosen specifically to avoid expanding the tool payload

How:

- keep `save_to_memory(fact)` as the primary tool interface for A1
- update system prompt text so the model understands Lulu has long-term memory with tagged canonical records
- update tool-result phrasing if needed so the assistant can acknowledge save/update outcomes naturally
- allow `MemoryManager.upsert_memory()` to return richer result info such as:
  - inserted vs updated
  - assigned tags
  - matched prior memory or not
- optionally expose that richer result to the assistant via tool message content so the final reply can be more precise without changing the user-visible core flow

### 5. `tests/test_llm_router.py`

What:

- expand focused unit coverage for the new behavior

Why:

- A1 materially changes persistence semantics and recall formatting, so regression coverage should target those decision points directly

How:

- keep the current router tests and extend them so fake memory results include tags/update outcomes
- add tests for:
  - explicit save still bypasses the chat reply path
  - tool-call save still works with the unchanged `fact` interface
  - router includes tag-aware context formatting in prompts if that formatting is surfaced through the memory manager mock

### 6. Add A New Focused Memory Test Module

Target file:

- `tests/test_memory_manager.py`

What:

- add focused unit tests around deduplication and tag metadata

Why:

- the bulk of A1 complexity lives in the memory layer, not the router

How:

- use fake embedding responses and controlled similarity scores so tests stay deterministic
- cover at minimum:
  - new memory insert creates tags metadata
  - near-duplicate memory updates the existing canonical record instead of creating another active record
  - conflicting new fact follows latest-wins behavior
  - classification fallback yields a safe tag when parsing fails
  - `format_context()` includes tags and source in retrieved memory lines

### 7. `README.md`

What:

- update the repo documentation to reflect the new memory semantics

Why:

- the current README mentions future deduplication/categories, but A1 would make them current behavior

How:

- update the hybrid memory section to explain:
  - semantic deduplication
  - backend-assigned free-form tags
  - latest-wins canonical updates
  - tag-aware recall context
- document any new environment variables for tuning dedup thresholds and tag limits
- keep the language simple and aligned with the Apple Silicon/local-first framing already in the repo

## Data Flow

### Save Flow

1. User says `insert info ...` or the LLM calls `save_to_memory(fact)`
2. Router validates the fact and calls `MemoryManager.upsert_memory(...)`
3. `MemoryManager` normalizes and embeds the fact
4. `MemoryManager` searches for a semantically similar candidate in the current Chroma collection
5. If similarity is below threshold:
   - classify 1-3 backend tags
   - insert a new memory record with metadata
6. If similarity is above threshold:
   - classify 1-3 backend tags for the new canonical text
   - update the existing canonical record with latest text, embedding, tags, and timestamps
7. Return structured save outcome to the router
8. Router emits a tool result or explicit-save acknowledgement

### Recall Flow

1. User asks a normal question
2. `MemoryManager.query_memory()` returns relevant records plus metadata
3. `format_context()` renders memory text together with tags and source
4. Router injects that formatted context into the system prompt
5. Ollama answers using the richer memory cues

## Edge Cases And Failure Modes

- empty or whitespace-only facts still fail validation
- if tag classification fails or returns unusable data, store the memory with a safe fallback tag instead of failing the save
- if dedup candidate retrieval is empty, always insert as new
- if multiple candidates are close, use the best semantic match only in A1; do not add multi-record conflict resolution yet
- if the new fact is similar but below threshold, prefer a new insert rather than an over-aggressive merge
- if a memory is updated, ensure the old canonical text is not still returned as a separate active record
- keep the one-tool-round guard in the router unchanged

## Verification Steps

Implementation should be considered complete only if all of the following pass:

1. `pytest -q`
2. focused diagnostics are clean for:
   - `config.py`
   - `memory_manager.py`
   - `ollama_client.py`
   - `llm_router.py`
   - `tests/test_memory_manager.py`
   - `tests/test_llm_router.py`
3. manual text-mode sanity checks confirm:
   - repeated save of the same fact updates instead of duplicating
   - changed fact for the same semantic slot follows latest-wins
   - retrieved prompt context shows tags alongside text
   - explicit `insert info ...` still bypasses normal chat generation

## Out Of Scope For A1

- wake-word or continuous listening
- chunked response streaming into TTS
- terminal UI
- revision history or human conflict-resolution flows
- multi-collection memory partitioning by category
- expanding the public save tool to carry tags directly
