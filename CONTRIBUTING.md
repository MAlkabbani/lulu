# Contributing

Thanks for helping improve Lulu VAIA.

## Before You Start

- read `README.md` for the current product baseline
- use `docs/architecture.md` and `docs/operations.md` to understand module boundaries and workflows
- keep changes surgical and aligned with the existing local-first Apple Silicon scope

## Development Setup

```bash
python3.12 -m venv .venv
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
python -m pytest -q
python -m compileall .
```

If you install `ruff` from `requirements-dev.txt`, you can also run:

```bash
ruff check .
```

## Issues And Roadmap

- use the bug report template for regressions and broken flows
- use the feature request template for roadmap ideas
- check `ROADMAP.md` before proposing larger direction changes
