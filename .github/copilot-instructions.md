# GitHub Copilot Instructions

Repository-wide guidance for GitHub Copilot (including Copilot Chat) in
OrchestrANT. This file is only Copilot's slice: the project's own rules,
invariants and CI contract live in [`AGENTS.md`](../AGENTS.md) and are not
restated here.

## About this repository

A pure Python project (`orchestrant/`), plus the LLM benchmark lab in
`benchmarks/` and the Reflex viewer in `frontend/`. `git ls-files` finds no
`.cpp`, `.hpp`, `.rs`, `.dart` or `.cmake` file, so there are no rules for
those languages here — add them only if such code ever lands. Cython is an
optional build step (`CYTHONIZE=True`) for wheels, never hand-written C.

- **Suggest freely in:** `orchestrant/`, `tests/`, `benchmarks/`, `bench/`,
  `docs/source/`, `scripts/`, `frontend/`.
- **Do not edit:** `third_party/` (submodule — fixes go upstream), `build/`,
  `dist/`, `output/`, `logs/`, `docs/test_results/` (all generated).
- **Workflows:** the CI lanes are reusable workflows in ANTfrastructure; the
  files under `.github/workflows/` are only their configuration. Do not
  re-inline lane steps.

## Tone

Precise, technical, short: one to three sentences plus the smallest example
that makes the point. English, like the rest of the repository.

## Typing policy

- Full annotations on every public function and class, including `-> None`.
- `from __future__ import annotations` at the top of every `.py` file.
- Modern syntax: `list[str]`, `dict[str, int]`, `X | None` — never `List`,
  `Dict`, `Optional`.
- No bare `Any`, no unparameterised generics, no `cast()` without a reason in a
  neighbouring comment.
- Typed error paths: explicit exception types, or `None`-returning signatures
  that say so.
- `# type: ignore` needs a comment saying why, every time.

## Commands

```bash
uv sync                       # dependencies
uv run ruff format .          # format (run often, not only before a commit)
uv run ruff check --fix .     # lint, auto-fixing what it can
uv run ty check .             # type check
uv run pytest tests/unit -v   # tests
```

`ruff` is pinned in `pyproject.toml` and mirrored in `.pre-commit-config.yaml`,
both following ANTfrastructure's `RUFF_VERSION`. Never raise one of those alone.

CI runs `ruff check --no-fix` and `ruff format --check --diff` — locally you
may and should use `--fix`. Findings get fixed, not silenced: no blanket
`noqa`, no speculative `per-file-ignores`; if a rule is genuinely wrong for
this project, disable that one rule in `pyproject.toml` with a reason.

**Static analysis is a merge blocker on both lanes** (codespell, bandit,
vulture, `ruff check`, `ruff format`, `ty`), and the how and why are in
[`AGENTS.md` § 4 *Pitfalls specific to this project*](../AGENTS.md#4-pitfalls-specific-to-this-project).
Read that section before suggesting a change to tooling or CI wiring.

## Tests

- New behaviour comes with unit tests that reuse the existing fixtures.
- Deterministic seeds, small inputs, no sleeps.
- The matrix runs 3.13, 3.14 and 3.14t; only the free-threaded build `3.14t`
  may fail without blocking CI. Do not extend that tolerance to other versions.

## Commits and PRs

- Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, …
- PR body: summary, changes, how to validate, CI status.

## Do / Don't

**Do** — small reviewable changes; annotate public APIs; propose tests; prefer
async-safe patterns around uvloop/uvicorn-style I/O (no blocking calls in event
loop callbacks); ask a clarifying question when the target Python version or
the intended backend is unclear.

**Don't** — no secrets or insecure defaults, ever; no large refactors without
discussion; no unused imports or variables; no code that `ruff check` rejects.
