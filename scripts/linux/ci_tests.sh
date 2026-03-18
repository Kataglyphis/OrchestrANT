#!/usr/bin/env bash
# ci_tests.sh - Run Python test matrix
# Uses shared modules from Kataglyphis-ContainerHub

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTAINERHUB_SCRIPTS="$SCRIPT_DIR/../../ExternalLib/Kataglyphis-ContainerHub/linux/scripts"

source "$CONTAINERHUB_SCRIPTS/01-core/python_uv.sh" || { echo "Error: failed to source python_uv.sh" >&2; exit 1; }

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: ci_tests.sh [package_name] [py_versions_string]"
  echo "  package_name defaults to \$PACKAGE_NAME or 'orchestr_ant_ion'"
  echo "  py_versions_string defaults to \$PY_VERSIONS or '3.13 3.14'"
  echo "  log file defaults to \$CI_TESTS_LOG_FILE or 'docs/test_results/ci_tests-<timestamp>.log'"
  exit 0
fi

PACKAGE_NAME="${1:-${PACKAGE_NAME:-orchestr_ant_ion}}"
PY_VERSIONS="${2:-${PY_VERSIONS:- 3.13 3.14}}"
EXPERIMENTAL_VERSIONS="${EXPERIMENTAL_VERSIONS:-3.14t}"

LOG_FILE="${CI_TESTS_LOG_FILE:-docs/test_results/ci_tests-$(timestamp).log}"
mkdir -p "$(dirname "$LOG_FILE")"

exec > >(tee -a "$LOG_FILE") 2>&1

info "Logging to: $LOG_FILE"
info "PACKAGE_NAME=$PACKAGE_NAME"
info "PY_VERSIONS=$PY_VERSIONS"
info "EXPERIMENTAL_VERSIONS=$EXPERIMENTAL_VERSIONS"

detect_workspace

mkdir -p "$WORKSPACE_ROOT/docs/test_results"

TEST_EXIT=0

for V in $PY_VERSIONS; do
  if is_experimental_python "$V"; then
    info "[experimental] Running Python $V in non-blocking mode"
  else
    info "[stable] Running Python $V"
  fi

  VENV_DIR="$WORKSPACE_ROOT/.venv-${V}"

  if is_experimental_python "$V"; then
    if ! uv_venv_create "$VENV_DIR" "$V"; then
      warn "[experimental] Failed to create venv for $V; continuing"
      continue
    fi
  else
    uv_venv_create "$VENV_DIR" "$V"
  fi

  uv_venv_activate "$VENV_DIR"

  if is_experimental_python "$V"; then
    if ! uv_sync_project --no-wxpython; then
      warn "[experimental] Failed to sync dependencies for $V; continuing"
      uv_venv_deactivate
      uv_venv_remove "$VENV_DIR"
      continue
    fi
  else
    uv_sync_project --no-wxpython
  fi

  if is_experimental_python "$V"; then
    uv_run pytest tests/unit -v \
      --cov="$PACKAGE_NAME" \
      --cov-report=term-missing \
      --cov-report="html:$WORKSPACE_ROOT/docs/test_results/coverage-html-${V}" \
      --cov-report="xml:$WORKSPACE_ROOT/docs/test_results/coverage-${V}.xml" \
      --junitxml="$WORKSPACE_ROOT/docs/test_results/report-${V}.xml" \
      --html="$WORKSPACE_ROOT/docs/test_results/pytest-report-${V}.html" \
      --self-contained-html \
      --md-report \
      --md-report-verbose=1 \
      --md-report-output "$WORKSPACE_ROOT/docs/test_results/pytest-report-${V}.md" \
      || warn "[experimental] Unit tests failed for $V; continuing"
  else
    uv_run pytest tests/unit -v \
      --cov="$PACKAGE_NAME" \
      --cov-report=term-missing \
      --cov-report="html:$WORKSPACE_ROOT/docs/test_results/coverage-html-${V}" \
      --cov-report="xml:$WORKSPACE_ROOT/docs/test_results/coverage-${V}.xml" \
      --junitxml="$WORKSPACE_ROOT/docs/test_results/report-${V}.xml" \
      --html="$WORKSPACE_ROOT/docs/test_results/pytest-report-${V}.html" \
      --self-contained-html \
      --md-report \
      --md-report-verbose=1 \
      --md-report-output "$WORKSPACE_ROOT/docs/test_results/pytest-report-${V}.md" || TEST_EXIT=$?
  fi

  uv_run python bench/demo_cprofile.py 2>/dev/null || info "demo_cprofile.py skipped"
  uv_run python bench/demo_line_profiler.py 2>/dev/null || info "demo_line_profiler.py skipped"
  uv_run -m memory_profiler bench/demo_memory_profiling.py 2>/dev/null || info "memory profiling skipped"

  uv_run py-spy record --rate 200 --duration 10 -o "$WORKSPACE_ROOT/docs/test_results/profile.svg" -- python bench/demo_py_spy.py 2>/dev/null \
    || info "py-spy profiling skipped (may require a longer-running process or py-spy missing)"

  uv_run pytest bench/demo_pytest_benchmark.py 2>/dev/null || info "benchmark tests skipped or failed"

  uv_venv_deactivate
  uv_venv_remove "$VENV_DIR"
done

exit "$TEST_EXIT"