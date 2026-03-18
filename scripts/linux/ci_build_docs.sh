#!/usr/bin/env bash
# ci_build_docs.sh - Build documentation with Sphinx
# Uses shared modules from Kataglyphis-ContainerHub

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINERHUB_SCRIPTS="$SCRIPT_DIR/../../ExternalLib/Kataglyphis-ContainerHub/linux/scripts"

source "$CONTAINERHUB_SCRIPTS/01-core/python_uv.sh" || { echo "Error: failed to source python_uv.sh" >&2; exit 1; }

COVERAGE_VERSION="${1:-3.13}"

detect_workspace

if [ -f "$WORKSPACE_ROOT/flutter/bin:$PATH" ]; then
  export PATH="$WORKSPACE_ROOT/flutter/bin:$PATH"
fi
git config --global --add safe.directory "$WORKSPACE_ROOT" || true

VENV_DIR="$WORKSPACE_ROOT/.venv-docs"

if [ -f "$VENV_DIR/bin/activate" ]; then
  info "Using existing docs venv at $VENV_DIR"
else
  info "Creating docs venv at $VENV_DIR"
  uv_venv_create "$VENV_DIR" "$COVERAGE_VERSION"
fi

uv_venv_activate "$VENV_DIR"
uv_sync_project --no-wxpython

cp "$WORKSPACE_ROOT/README.md" "$WORKSPACE_ROOT/docs/source/README.md" 2>/dev/null || true
cp "$WORKSPACE_ROOT/CHANGELOG.md" "$WORKSPACE_ROOT/docs/source/CHANGELOG.md" 2>/dev/null || true

SRC="$WORKSPACE_ROOT/docs/test_results"
STATIC_DIR="$WORKSPACE_ROOT/docs/source/_static"
COVERAGE_DST=$STATIC_DIR/coverage
TEST_RESULTS_DST=$STATIC_DIR/test_results

mkdir -p "$COVERAGE_DST" "$TEST_RESULTS_DST"

info "Looking for coverage HTML in $SRC ..."
if [ -d "$SRC/coverage-html-${COVERAGE_VERSION}" ]; then
  cp -r "$SRC/coverage-html-${COVERAGE_VERSION}/." "$COVERAGE_DST/"
  info "Copied $SRC/coverage-html-${COVERAGE_VERSION} -> $COVERAGE_DST/"
elif [ -d "$SRC/coverage" ]; then
  cp -r "$SRC/coverage/." "$COVERAGE_DST/"
  info "Copied $SRC/coverage -> $COVERAGE_DST/"
elif [ -d "$SRC/htmlcov" ]; then
  cp -r "$SRC/htmlcov/." "$COVERAGE_DST/"
  info "Copied $SRC/htmlcov -> $COVERAGE_DST/"
else
  found=$(find "$SRC" -maxdepth 3 -type f -name index.html | grep -v pytest-report | head -n1 || true)
  if [ -n "$found" ]; then
    base=$(dirname "$found")
    cp -r "$base/." "$COVERAGE_DST/"
    info "Copied discovered coverage HTML from $base -> $COVERAGE_DST/"
  else
    warn "No coverage HTML folder found in $SRC."
  fi
fi

if [ -f "$SRC/coverage-${COVERAGE_VERSION}.xml" ]; then
  cp "$SRC/coverage-${COVERAGE_VERSION}.xml" "$STATIC_DIR/coverage.xml"
  info "Copied coverage-${COVERAGE_VERSION}.xml -> $STATIC_DIR/coverage.xml"
elif [ -f "$SRC/coverage.xml" ]; then
  cp "$SRC/coverage.xml" "$STATIC_DIR/coverage.xml"
  info "Copied coverage.xml -> $STATIC_DIR/coverage.xml"
fi

info "Copying pytest HTML reports..."
for html_file in "$SRC"/pytest-report-*.html; do
  if [ -f "$html_file" ]; then
    cp "$html_file" "$TEST_RESULTS_DST/"
    info "Copied $(basename "$html_file") to $TEST_RESULTS_DST/"
  fi
done

for xml_file in "$SRC"/report-*.xml; do
  if [ -f "$xml_file" ]; then
    cp "$xml_file" "$TEST_RESULTS_DST/"
    info "Copied $(basename "$xml_file") to $TEST_RESULTS_DST/"
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
  info "Fixing ownership of docs to ${OWNER_UID}:${OWNER_GID}"
  chown -R ${OWNER_UID}:${OWNER_GID} "$WORKSPACE_ROOT/docs" || true
fi