# Contributing

Thanks for helping improve Lulu VAIA.

## Before You Start

- read `README.md` for the current product baseline
- use `docs/architecture.md` and `docs/operations.md` to understand module boundaries and workflows
- keep changes surgical and aligned with the existing local-first Apple Silicon scope

## Development Setup

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements-dev.txt
```

## Development Expectations

- preserve local-only behavior unless a change explicitly requires something broader
- avoid widening the backend tool surface without strong validation and documentation
- update docs when behavior, operator workflow, or contributor workflow changes
- add focused tests when they materially reduce regression risk
- keep user-facing terminal language clear and concise

## Pull Request Checklist

- describe the problem and the behavior change clearly
- keep the PR scoped to one logical improvement
- update tests or explain why extra coverage is not needed
- update `README.md`, `docs/`, or both when public behavior changes
- note any follow-up work that should happen separately

## Verification

Run the focused checks that match your change:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check .
bash -n scripts/install_lulu.sh scripts/start_lulu.sh
```

If your change touches the desktop shell, also run:

```bash
cd macos_app
swiftc -typecheck Sources/LuluApp/App/*.swift \
  Sources/LuluApp/Features/Assistant/*.swift \
  Sources/LuluApp/Features/Diagnostics/*.swift \
  Sources/LuluApp/Features/PDFAudiobooks/*.swift \
  Sources/LuluApp/Features/Settings/*.swift \
  Sources/LuluApp/Models/*.swift \
  Sources/LuluApp/Services/*.swift
```

## Issues And Roadmap

- use the bug report template for regressions and broken flows
- use the feature request template for roadmap ideas
- check `ROADMAP.md` before proposing larger direction changes
