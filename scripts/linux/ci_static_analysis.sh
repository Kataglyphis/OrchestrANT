#!/usr/bin/env bash
# ci_static_analysis.sh - Run Python static analysis tools
# Uses shared modules from Kataglyphis-ContainerHub

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINERHUB_SCRIPTS="$SCRIPT_DIR/../../ExternalLib/Kataglyphis-ContainerHub/linux/scripts"

source "$CONTAINERHUB_SCRIPTS/01-core/python_uv.sh" || { echo "Error: failed to source python_uv.sh" >&2; exit 1; }

ARCH="${1:-}"
PYTHON_VERSION="${2:-3.14}"
PACKAGE_NAME="${3:-orchestr_ant_ion}"

info "Using Python version: $PYTHON_VERSION"
info "Running static analysis for package: $PACKAGE_NAME"

detect_workspace

VENV_DIR="$WORKSPACE_ROOT/.venv_static_analysis"
VENV_WAS_PRESENT=0

if [ -d "$VENV_DIR" ]; then
  info "Using existing virtual environment at: $VENV_DIR"
  VENV_WAS_PRESENT=1
  uv_venv_activate "$VENV_DIR"
else
  info "Creating virtual environment with Python $PYTHON_VERSION at: $VENV_DIR"
  UV_VENV_CLEAR=1 uv_venv_create "$VENV_DIR" "$PYTHON_VERSION"
fi

uv_sync_project --no-wxpython

uv_run codespell "$PACKAGE_NAME" tests docs/source/conf.py setup.py README.md 2>/dev/null || true
uv run --active bandit -r "$PACKAGE_NAME" \
  -x tests,.venv,.venv_static_analysis,ExternalLib,archive,docs/test_results 2>/dev/null || true
uv run --active vulture "$PACKAGE_NAME" tests docs/source/conf.py setup.py 2>/dev/null || true
uv run --active ruff check --fix "$PACKAGE_NAME" tests docs/source/conf.py setup.py || true
uv run --active ruff format "$PACKAGE_NAME" tests docs/source/conf.py setup.py || true
uv run --active ty check 2>/dev/null || true

if [ "$VENV_WAS_PRESENT" -eq 0 ]; then
  uv_venv_remove "$VENV_DIR"
fi

if [ -n "$ARCH" ]; then
  info "Static analysis completed for arch: $ARCH"
fi