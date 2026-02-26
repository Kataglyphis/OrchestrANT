#!/usr/bin/env bash
set -euo pipefail

ARCH="${1:-}"  # optional; kept for parity with workflow matrix

git config --global --add safe.directory /workspace || true

VENV_DIR=".venv_static_analysis"
VENV_WAS_PRESENT=0

if [ -d "$VENV_DIR" ]; then
  echo "Using existing virtual environment at: $VENV_DIR"
  VENV_WAS_PRESENT=1
else
  echo "Creating virtual environment at: $VENV_DIR"
  UV_VENV_CLEAR=1 uv venv "$VENV_DIR"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

if [ -f uv.lock ]; then
  echo "uv.lock found — using locked sync"
  uv -v sync --active --locked --dev --all-extras --no-build-isolation-package wxpython
else
  echo "No uv.lock found — performing non-locked sync"
  uv -v sync --active --dev --all-extras --no-build-isolation-package wxpython
fi

uv run --active codespell orchestr_ant_ion tests docs/source/conf.py setup.py README.md || true
# uv run --active mypy orchestr_ant_ion tests docs/source/conf.py setup.py || true
uv run --active bandit -r orchestr_ant_ion \
  -x tests,.venv,.venv_static_analysis,ExternalLib,archive,docs/test_results || true
uv run --active vulture orchestr_ant_ion tests docs/source/conf.py setup.py || true
uv run --active ruff check --fix orchestr_ant_ion tests docs/source/conf.py setup.py || true
uv run --active ruff format orchestr_ant_ion tests docs/source/conf.py setup.py || true
uv run --active ty check || true

if [ "$VENV_WAS_PRESENT" -eq 0 ]; then
  rm -rf "$VENV_DIR"
fi

if [ -n "$ARCH" ]; then
  echo "Static analysis completed for arch: $ARCH"
fi
