#!/usr/bin/env bash
# ci_packaging.sh - Build Python packages (source and binary wheels)
# Uses shared modules from Kataglyphis-ContainerHub

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINERHUB_SCRIPTS="$SCRIPT_DIR/../../ExternalLib/Kataglyphis-ContainerHub/linux/scripts"

source "$CONTAINERHUB_SCRIPTS/01-core/python_uv.sh" || { echo "Error: failed to source python_uv.sh" >&2; exit 1; }

PYTHON_VERSION="${1:-3.14}"
info "Using Python version: $PYTHON_VERSION"

detect_workspace

if [ -f "$WORKSPACE_ROOT/flutter/bin:$PATH" ]; then
  export PATH="$WORKSPACE_ROOT/flutter/bin:$PATH"
fi
git config --global --add safe.directory "$WORKSPACE_ROOT" || true

if command -v patchelf >/dev/null 2>&1; then
  info "patchelf already installed"
else
  SUDO_CMD=""
  if command -v sudo >/dev/null 2>&1; then
    SUDO_CMD="sudo"
  fi
  $SUDO_CMD apt-get update
  $SUDO_CMD apt-get install -y patchelf
fi

VENV_SOURCES="$WORKSPACE_ROOT/.venv_packaging_sources"
if [ -f "$VENV_SOURCES/bin/activate" ]; then
  info "Using existing source packaging venv at $VENV_SOURCES"
  uv_venv_activate "$VENV_SOURCES"
else
  info "Creating source packaging venv with Python $PYTHON_VERSION at $VENV_SOURCES"
  uv_venv_create "$VENV_SOURCES" "$PYTHON_VERSION"
fi

uv_sync_project --no-wxpython

uv build

export CYTHONIZE="True"

VENV_BINARIES="$WORKSPACE_ROOT/.venv_packaging_binaries"
if [ -f "$VENV_BINARIES/bin/activate" ]; then
  info "Using existing binary packaging venv at $VENV_BINARIES"
  uv_venv_activate "$VENV_BINARIES"
else
  info "Creating binary packaging venv with Python $PYTHON_VERSION at $VENV_BINARIES"
  uv_venv_create "$VENV_BINARIES" "$PYTHON_VERSION"
fi

uv_sync_project --no-wxpython

uv build

mkdir -p dist repaired
shopt -s nullglob
info "Found wheels:"
ls -la dist || true

for whl in dist/*.whl; do
  info "Inspecting wheel: $whl"
  if auditwheel show "$whl" >/dev/null 2>&1; then
    info "  Platform wheel detected -> repairing: $whl"
    auditwheel repair "$whl" -w repaired/ || { error "auditwheel failed on $whl"; exit 1; }
  else
    info "  Pure/Python wheel detected -> copying unchanged: $whl"
    cp "$whl" repaired/
  fi
done

rm -f dist/*.whl || true
mv repaired/*.whl dist/ || true
rmdir repaired || true

info "Final wheels in dist/:"
ls -la dist || true