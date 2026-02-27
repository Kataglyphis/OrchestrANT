#!/usr/bin/env bash

ARCH="${1:-}"  # optional; kept for parity with workflow matrix
PYTHON_VERSION="${2:-3.14}" # optional Python version, defaults to 3.14
PACKAGE_NAME="${3:-orchestr_ant_ion}" # optional package name, defaults to orchestr_ant_ion

echo "Using Python version: $PYTHON_VERSION"
echo "Running static analysis for package: $PACKAGE_NAME"

git config --global --add safe.directory /workspace || true

VENV_DIR=".venv_static_analysis"
VENV_WAS_PRESENT=0

if [ -d "$VENV_DIR" ]; then
  echo "Using existing virtual environment at: $VENV_DIR"
  VENV_WAS_PRESENT=1
else
  echo "Creating virtual environment with Python $PYTHON_VERSION at: $VENV_DIR"
  UV_VENV_CLEAR=1 uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
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

uv run --active codespell "$PACKAGE_NAME" tests docs/source/conf.py setup.py README.md || true
# uv run --active mypy "$PACKAGE_NAME" tests docs/source/conf.py setup.py || true
uv run --active bandit -r "$PACKAGE_NAME" \
  -x tests,.venv,.venv_static_analysis,ExternalLib,archive,docs/test_results || true
uv run --active vulture "$PACKAGE_NAME" tests docs/source/conf.py setup.py || true
uv run --active ruff check --fix "$PACKAGE_NAME" tests docs/source/conf.py setup.py || true
uv run --active ruff format "$PACKAGE_NAME" tests docs/source/conf.py setup.py || true
uv run --active ty check || true

if [ "$VENV_WAS_PRESENT" -eq 0 ]; then
  rm -rf "$VENV_DIR"
fi

if [ -n "$ARCH" ]; then
  echo "Static analysis completed for arch: $ARCH"
fi
