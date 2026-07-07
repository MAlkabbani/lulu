# Roadmap

This roadmap keeps Lulu focused on a practical goal: a reliable, extensible local voice assistant starter that contributors can improve without fighting the architecture.

## Now

- keep the local Apple Silicon runtime stable and well-documented
- improve contributor onboarding, issue triage, and release hygiene
- harden configuration, startup, and operator-visible failure handling
- finish Stage 5 desktop packaging-readiness work without weakening preview-mode or local IPC trust boundaries

## Next

- replace or augment macOS `say` with higher-quality local TTS
- add more realistic wake benchmarking and calibration workflows
- move from Stage 5 packaging-readiness into repeatable signed-release and clean-machine validation work
- expand test coverage around scripts, startup, and failure recovery paths

## Later

- add optional interruption and barge-in support during playback
- support richer memory inspection and review workflows
- introduce optional plugin-style tool expansion while preserving strict validation
- explore broader platform support without weakening the Apple Silicon-first path

## Contribution Fit

Good community contributions usually fit one of these lanes:

- reliability improvements with focused regression coverage
- documentation alignment and clearer operator workflows
- observability improvements in the terminal dashboard
- carefully scoped extensibility improvements that preserve local-first guarantees
