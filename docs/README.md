# Lulu Documentation Index

This directory is the public, version-controlled documentation surface for Lulu VAIA.

## Source Of Truth

- [Installation And Operations Runbook](./operations.md)
- [Product Requirements Document](./prd.md)
- [Decision Log](./decision-log.md)
- [Wake Performance Report](./wake-performance-report.md)
- [Project Blueprint](../Project_Blueprint_AI_Assistant.md)

## Current Product Baseline

The current documented product baseline includes:

- fully local Apple Silicon-first runtime on macOS
- hybrid memory routing with explicit and autonomous save paths
- canonical semantic memory deduplication with backend tagging
- grouped, smoothness-first streamed TTS on top of native macOS `say`
- continuous listening with acoustic preprocessing, DTW-assisted wake matching, transcript confirmation, cooldown, self-audio suppression, and optional practical voice tuning
- terminal observability for runtime mode, speech continuity, latency, memory, and wake diagnostics

## Engineering Plans And Implementation History

These tracked implementation plans explain how major shipped milestones were scoped and verified:

- [A1 Memory Deduplication And Categories](./implementation-plans/a1-memory-deduplication-and-categories.md)
- [B1 Chunked TTS Streaming](./implementation-plans/b1-chunked-tts-streaming.md)
- [C1 Wake-Word And Continuous Listening](./implementation-plans/c1-wake-word-continuous-listening.md)

These plans are historical milestone records. When the shipped runtime later evolves beyond an original plan, treat `README.md`, `docs/prd.md`, and `docs/decision-log.md` as the current source of truth for present-day behavior.

## How To Use These Docs

- Start with the PRD if you need product scope, requirements, user stories, acceptance criteria, and success metrics.
- Read the operations runbook if you need production-style install, startup, supervision, configuration, or rollback behavior.
- Read the decision log if you need to understand why a technical or product direction was chosen.
- Use the project blueprint for the original product vision and stack constraints.
- Use the implementation plans when planning incremental changes against shipped milestones.
- Treat `.trae/` as local workspace metadata rather than the public documentation surface.
