#!/usr/bin/env bash
set -euo pipefail

export PATH="$PWD/flutter/bin:$PATH"
git config --global --add safe.directory /workspace || true

sudo apt-get update
sudo apt-get install -y patchelf

VENV_SOURCES=".venv_packaging_sources"
uv venv "$VENV_SOURCES"

# shellcheck disable=SC1090
source "$VENV_SOURCES/bin/activate"

if [ -f uv.lock ]; then
  echo "uv.lock found — using locked sync"
  uv -v sync --locked --dev --all-extras --no-build-isolation-package wxpython
else
  echo "No uv.lock found — performing non-locked sync"
  uv -v sync --dev --all-extras --no-build-isolation-package wxpython
fi

uv build

export CYTHONIZE="True"

VENV_BINARIES=".venv_packaging_binaries"
uv venv "$VENV_BINARIES"

# shellcheck disable=SC1090
source "$VENV_BINARIES/bin/activate"

if [ -f uv.lock ]; then
  echo "uv.lock found — using locked sync"
  uv -v sync --locked --dev --all-extras --no-build-isolation-package wxpython
else
  echo "No uv.lock found — performing non-locked sync"
  uv -v sync --dev --all-extras --no-build-isolation-package wxpython
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

rm -rf "$VENV_SOURCES" "$VENV_BINARIES"
