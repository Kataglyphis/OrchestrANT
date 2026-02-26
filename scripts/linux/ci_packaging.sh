#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$REPO_ROOT}"

if [ -d /workspace ] && [ -f /workspace/pyproject.toml ]; then
  WORKSPACE_ROOT="/workspace"
fi

cd "$WORKSPACE_ROOT"

export PATH="$WORKSPACE_ROOT/flutter/bin:$PATH"
git config --global --add safe.directory "$WORKSPACE_ROOT" || true

if command -v patchelf >/dev/null 2>&1; then
  echo "patchelf already installed"
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
  echo "Using existing source packaging venv at $VENV_SOURCES"
else
  echo "Creating source packaging venv at $VENV_SOURCES"
  uv venv "$VENV_SOURCES"
fi

# shellcheck disable=SC1090
source "$VENV_SOURCES/bin/activate"

if [ -f uv.lock ]; then
  echo "uv.lock found — using locked sync"
  uv -v sync --active --locked --dev --all-extras --no-build-isolation-package wxpython
else
  echo "No uv.lock found — performing non-locked sync"
  uv -v sync --active --dev --all-extras --no-build-isolation-package wxpython
fi

uv build

export CYTHONIZE="True"

VENV_BINARIES=".venv_packaging_binaries"
VENV_BINARIES="$WORKSPACE_ROOT/.venv_packaging_binaries"
if [ -f "$VENV_BINARIES/bin/activate" ]; then
  echo "Using existing binary packaging venv at $VENV_BINARIES"
else
  echo "Creating binary packaging venv at $VENV_BINARIES"
  uv venv "$VENV_BINARIES"
fi

# shellcheck disable=SC1090
source "$VENV_BINARIES/bin/activate"

if [ -f uv.lock ]; then
  echo "uv.lock found — using locked sync"
  uv -v sync --active --locked --dev --all-extras --no-build-isolation-package wxpython
else
  echo "No uv.lock found — performing non-locked sync"
  uv -v sync --active --dev --all-extras --no-build-isolation-package wxpython
fi

uv build

mkdir -p dist repaired
shopt -s nullglob
echo "Found wheels:"
ls -la dist || true

for whl in dist/*.whl; do
  echo "Inspecting wheel: $whl"
  if auditwheel show "$whl" >/dev/null 2>&1; then
    echo "  Platform wheel detected -> repairing: $whl"
    auditwheel repair "$whl" -w repaired/ || { echo "auditwheel failed on $whl"; exit 1; }
  else
    echo "  Pure/Python wheel detected -> copying unchanged: $whl"
    cp "$whl" repaired/
  fi
done

rm -f dist/*.whl || true
mv repaired/*.whl dist/ || true
rmdir repaired || true

echo "Final wheels in dist/:"
ls -la dist || true

