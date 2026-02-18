#!/usr/bin/env bash
set -euo pipefail

ARCH="${1:-}"  # optional; kept for parity with workflow matrix

git config --global --add safe.directory /workspace || true

VENV_DIR=".venv_static_analysis"
uv venv "$VENV_DIR"

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

if [ -f uv.lock ]; then
  echo "uv.lock found — using locked sync"
  uv -v sync --locked --dev --all-extras --no-build-isolation-package wxpython
else
  echo "No uv.lock found — performing non-locked sync"
  uv -v sync --dev --all-extras --no-build-isolation-package wxpython
fi

uv run codespell || true
uv run mypy . || true
uv run bandit -r . || true
uv run vulture . || true
uv run ruff check || true
uv run ty check || true

rm -rf "$VENV_DIR"

if [ -n "$ARCH" ]; then
  echo "Static analysis completed for arch: $ARCH"
fi
