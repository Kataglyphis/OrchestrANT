#!/usr/bin/env bash
set -euo pipefail

COVERAGE_VERSION="${1:-3.13}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$REPO_ROOT}"

if [ -d /workspace ] && [ -f /workspace/pyproject.toml ]; then
  WORKSPACE_ROOT="/workspace"
fi

export PATH="$WORKSPACE_ROOT/flutter/bin:$PATH"
git config --global --add safe.directory "$WORKSPACE_ROOT" || true

VENV_DIR="$WORKSPACE_ROOT/.venv-docs"

if [ -f "$VENV_DIR/bin/activate" ]; then
  echo "Using existing docs venv at $VENV_DIR"
else
  echo "Creating docs venv at $VENV_DIR"
  uv venv "$VENV_DIR"
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

cp "$WORKSPACE_ROOT/README.md" "$WORKSPACE_ROOT/docs/source/README.md"
cp "$WORKSPACE_ROOT/CHANGELOG.md" "$WORKSPACE_ROOT/docs/source/CHANGELOG.md"

SRC="$WORKSPACE_ROOT/docs/test_results"
STATIC_DIR="$WORKSPACE_ROOT/docs/source/_static"
COVERAGE_DST=$STATIC_DIR/coverage
TEST_RESULTS_DST=$STATIC_DIR/test_results

mkdir -p "$COVERAGE_DST" "$TEST_RESULTS_DST"

echo "Looking for coverage HTML in $SRC ..."
if [ -d "$SRC/coverage-html-${COVERAGE_VERSION}" ]; then
  cp -r "$SRC/coverage-html-${COVERAGE_VERSION}/." "$COVERAGE_DST/"
  echo "Copied $SRC/coverage-html-${COVERAGE_VERSION} -> $COVERAGE_DST/"
elif [ -d "$SRC/coverage" ]; then
  cp -r "$SRC/coverage/." "$COVERAGE_DST/"
  echo "Copied $SRC/coverage -> $COVERAGE_DST/"
elif [ -d "$SRC/htmlcov" ]; then
  cp -r "$SRC/htmlcov/." "$COVERAGE_DST/"
  echo "Copied $SRC/htmlcov -> $COVERAGE_DST/"
else
  found=$(find "$SRC" -maxdepth 3 -type f -name index.html | grep -v pytest-report | head -n1 || true)
  if [ -n "$found" ]; then
    base=$(dirname "$found")
    cp -r "$base/." "$COVERAGE_DST/"
    echo "Copied discovered coverage HTML from $base -> $COVERAGE_DST/"
  else
    echo "No coverage HTML folder found in $SRC."
  fi
fi

if [ -f "$SRC/coverage-${COVERAGE_VERSION}.xml" ]; then
  cp "$SRC/coverage-${COVERAGE_VERSION}.xml" "$STATIC_DIR/coverage.xml"
  echo "Copied coverage-${COVERAGE_VERSION}.xml -> $STATIC_DIR/coverage.xml"
elif [ -f "$SRC/coverage.xml" ]; then
  cp "$SRC/coverage.xml" "$STATIC_DIR/coverage.xml"
  echo "Copied coverage.xml -> $STATIC_DIR/coverage.xml"
fi

echo "Copying pytest HTML reports..."
for html_file in "$SRC"/pytest-report-*.html; do
  if [ -f "$html_file" ]; then
    cp "$html_file" "$TEST_RESULTS_DST/"
    echo "Copied $(basename "$html_file") to $TEST_RESULTS_DST/"
  fi
done

for xml_file in "$SRC"/report-*.xml; do
  if [ -f "$xml_file" ]; then
    cp "$xml_file" "$TEST_RESULTS_DST/"
    echo "Copied $(basename "$xml_file") to $TEST_RESULTS_DST/"
  fi
done

for md_file in "$SRC"/pytest-report-*.md; do
  if [ -f "$md_file" ]; then
    cp "$md_file" "$STATIC_DIR/"
  fi
done

cd "$WORKSPACE_ROOT/docs"
make html

if [ "$WORKSPACE_ROOT" = "/workspace" ] && [ -d /workspace ]; then
  OWNER_UID=$(stat -c "%u" /workspace)
  OWNER_GID=$(stat -c "%g" /workspace)
  echo "Fixing ownership of docs to ${OWNER_UID}:${OWNER_GID}"
  chown -R ${OWNER_UID}:${OWNER_GID} "$WORKSPACE_ROOT/docs" || true
fi
