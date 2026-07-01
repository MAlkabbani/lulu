# Lulu Documentation Index

This directory is the public, version-controlled documentation surface for Lulu VAIA.

## Source Of Truth

- [Product Requirements Document](./prd.md)
- [Decision Log](./decision-log.md)
- [Project Blueprint](../Project_Blueprint_AI_Assistant.md)

## Current Product Baseline

The current documented product baseline includes:

- fully local Apple Silicon-first runtime on macOS
- hybrid memory routing with explicit and autonomous save paths
- canonical semantic memory deduplication with backend tagging
- phrase-boundary streamed TTS on top of native macOS `say`
- continuous listening with wake phrase detection, cooldown, and self-audio suppression
- terminal observability for runtime mode, latency, memory, and wake diagnostics

## Engineering Plans And Implementation History

These implementation plans explain how major shipped milestones were scoped and verified:

- [A1 Memory Deduplication And Categories](../.trae/documents/a1-memory-deduplication-and-categories-plan.md)
- [B1 Chunked TTS Streaming](../.trae/documents/b1-chunked-tts-streaming-plan.md)
- [C1 Wake-Word And Continuous Listening](../.trae/documents/c1-wake-word-continuous-listening-plan.md)

## How To Use These Docs

- Start with the PRD if you need product scope, requirements, user stories, acceptance criteria, and success metrics.
- Read the decision log if you need to understand why a technical or product direction was chosen.
- Use the project blueprint for the original product vision and stack constraints.
- Use the implementation plans when planning incremental changes against shipped milestones.
