#!/usr/bin/env bash
set -euo pipefail

timestamp() {
  date +%Y%m%d-%H%M%S
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: ci_tests.sh [package_name] [py_versions_string]"
  echo "  package_name defaults to \$PACKAGE_NAME or 'orchestr_ant_ion'"
  echo "  py_versions_string defaults to \$PY_VERSIONS or '3.11 3.12 3.13 3.14 3.14t'"
  echo "  log file defaults to \$CI_TESTS_LOG_FILE or 'docs/test_results/ci_tests-<timestamp>.log'"
  exit 0
fi

PACKAGE_NAME="${1:-${PACKAGE_NAME:-orchestr_ant_ion}}"
PY_VERSIONS="${2:-${PY_VERSIONS:- 3.11 3.12 3.13 3.14 3.14t}}"

LOG_FILE="${CI_TESTS_LOG_FILE:-docs/test_results/ci_tests-$(timestamp).log}"
mkdir -p "$(dirname "$LOG_FILE")"

# Mirror everything to file + terminal
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Logging to: $LOG_FILE"
echo "PACKAGE_NAME=$PACKAGE_NAME"
echo "PY_VERSIONS=$PY_VERSIONS"

git config --global --add safe.directory /workspace || true

# Ensure test results dir exists
mkdir -p docs/test_results

TEST_EXIT=0

for V in $PY_VERSIONS; do
  VENV_DIR=".venv-${V}"
  uv venv "$VENV_DIR" --python="${V}"

  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"

  if [ -f uv.lock ]; then
    echo "uv.lock found — using locked sync"
    uv -v sync --active --locked --dev --all-extras #--no-build-isolation-package wxpython
  else
    echo "No uv.lock found — performing non-locked sync"
    uv -v sync --active --dev --all-extras #  --no-build-isolation-package wxpython
  fi

  uv run --active pytest tests/unit -v \
    --cov="$PACKAGE_NAME" \
    --cov-report=term-missing \
    --cov-report="html:docs/test_results/coverage-html-${V}" \
    --cov-report="xml:docs/test_results/coverage-${V}.xml" \
    --junitxml="docs/test_results/report-${V}.xml" \
    --html="docs/test_results/pytest-report-${V}.html" \
    --self-contained-html \
    --md-report \
    --md-report-verbose=1 \
    --md-report-output "docs/test_results/pytest-report-${V}.md" || TEST_EXIT=$?

  uv run --active python bench/demo_cprofile.py || echo "demo_cprofile.py skipped"
  uv run --active python bench/demo_line_profiler.py || echo "demo_line_profiler.py skipped"
  uv run --active -m memory_profiler bench/demo_memory_profiling.py || echo "memory profiling skipped"

  uv run --active py-spy record --rate 200 --duration 10 -o docs/test_results/profile.svg -- python bench/demo_py_spy.py \
    || echo "py-spy profiling skipped (may require a longer-running process or py-spy missing)"

  uv run --active pytest bench/demo_pytest_benchmark.py || echo "benchmark tests skipped or failed"

  deactivate || true
  rm -rf "$VENV_DIR"
done

exit "$TEST_EXIT"
