#!/usr/bin/env bash
# ci_static_analysis.sh - the Linux static-analysis GATE.
#
# WHY THIS IS NOT `containerhub_exec` LIKE ITS THREE SIBLINGS
# ----------------------------------------------------------
# It used to be. ContainerHub's generic driver
# (linux/scripts/02-toolchain/python/ci_static_analysis.sh) ends EVERY tool
# line with `|| true`:
#
#     uv_run codespell ... 2>/dev/null || true
#     uv_run --active bandit -r ... 2>/dev/null || true
#     uv_run --active vulture ... 2>/dev/null || true
#     uv_run --active ruff check --fix ... || true
#     uv_run --active ruff format ... || true
#     uv_run --active ty check 2>/dev/null || true
#
# so it exits 0 no matter what the five tools find. Delegating to it meant the
# "Python static analysis" step of the reusable Linux lane could not fail, and
# .github/copilot-instructions.md's claim that ruff and ty are merge blockers
# was false on this lane. Measured on this tree: ruff alone had 63 findings
# while the step was green.
#
# Two further layers of the same problem lived in those lines and are fixed
# below rather than reproduced:
#   * `ruff check --FIX` reports only the violations it could NOT fix. In CI the
#     checkout is thrown away afterwards, so every auto-fixable finding was
#     repaired into a directory nobody keeps and never surfaced. One of this
#     repo's real findings (I001, un-sorted imports) is auto-fixable and was
#     therefore invisible twice over.
#   * `ruff format` without --check REWRITES files and always exits 0, so a
#     mis-formatted file that is committed stays committed.
#   * `2>/dev/null` on four of the six discarded the diagnostics themselves, so
#     even reading the log told you nothing.
#
# Fixing that upstream is a ContainerHub change; until the driver stops
# swallowing, this repo owns its own gating policy. Everything expensive is
# still the hub's: ci-common.sh brings detect_workspace, the uv_venv_* lifecycle
# and uv_sync_project via 01-core/python_uv.sh. Only the ~20 lines that decide
# what "failed" means are local.
#
# When the upstream driver gates, delete this file's body and go back to
#   containerhub_exec "linux/scripts/02-toolchain/python/ci_static_analysis.sh" "$@"
# after checking that it still runs `ruff check` without --fix and
# `ruff format --check`.
#
# Usage: ci_static_analysis.sh [arch] [python_version] [package_name]
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/containerhub.sh"

# PACKAGE_NAME is set explicitly rather than left to upstream's
# derive_package_name: that reads the DISTRIBUTION name from pyproject.toml
# ("OrchestrANT"), but bandit/ruff/vulture/pytest --cov all want the
# importable MODULE directory, which is "orchestrant". The two differ in
# this project, so deriving would point every tool at a path that does not exist.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestrant}"

# Pin the workspace BEFORE the hub library loads, for the same reason
# containerhub_exec does it: upstream's detect_workspace derives the root from
# the sourcing script's location, and a hub file sourced from here resolves
# inside third_party/ContainerHub. detect_workspace honours a pre-set value and
# still overrides to /workspace in the container.
export WORKSPACE_ROOT="${WORKSPACE_ROOT:-$KATAGLYPHIS_REPO_ROOT}"

containerhub_source "linux/scripts/02-toolchain/python/ci-common.sh"

ARCH="${1:-${ARCH:-}}"
PYTHON_VERSION="${2:-${PYTHON_VERSION:-3.14}}"
PACKAGE_NAME="$(derive_package_name "${3:-${PACKAGE_NAME:-}}")"

detect_workspace
cd "$WORKSPACE_ROOT"

info "Using Python version: $PYTHON_VERSION"
info "Running static analysis for package: $PACKAGE_NAME"

# No `|| true`: if git cannot mark the workspace safe, every tool that shells
# out to git below is about to misbehave in a way that is much harder to read
# than this failure.
git config --global --add safe.directory "$WORKSPACE_ROOT"

VENV_DIR="$WORKSPACE_ROOT/.venv_static_analysis"
UV_VENV_CLEAR=1 uv_venv_ensure "$VENV_DIR" "$PYTHON_VERSION" "static-analysis venv" VENV_WAS_PRESENT

uv_sync_project --no-wxpython

# Every tool RUNS even when an earlier one failed, so one PR sees every finding
# instead of one per push; the exit code is decided once, at the end.
GATE_FAILURES=()

run_gate() {
  local name="$1"
  shift
  info "=== ${name} ==="
  if "$@"; then
    info "=== ${name}: ok ==="
  else
    local status=$?
    warn "=== ${name}: FAILED (exit ${status}) ==="
    GATE_FAILURES+=("${name}")
  fi
}

run_gate "codespell" uv_run codespell \
  "$PACKAGE_NAME" tests docs/source/conf.py setup.py README.md
run_gate "bandit" uv_run bandit -r "$PACKAGE_NAME" \
  -x tests,.venv,.venv_static_analysis,ExternalLib,third_party,archive,docs/test_results
run_gate "vulture" uv_run vulture \
  "$PACKAGE_NAME" tests docs/source/conf.py setup.py
# --no-fix, not --fix: the gate must judge the tree as committed.
run_gate "ruff check" uv_run ruff check --no-fix \
  "$PACKAGE_NAME" tests docs/source/conf.py setup.py
# --check --diff, not a bare `format`: report, do not rewrite.
run_gate "ruff format" uv_run ruff format --check --diff \
  "$PACKAGE_NAME" tests docs/source/conf.py setup.py
run_gate "ty" uv_run ty check

if [ "$VENV_WAS_PRESENT" -eq 0 ]; then
  uv_venv_remove "$VENV_DIR"
fi

if [ -n "$ARCH" ]; then
  info "Static analysis completed for arch: $ARCH"
fi

if [ ${#GATE_FAILURES[@]} -gt 0 ]; then
  err "Static analysis FAILED: ${GATE_FAILURES[*]}"
  exit 1
fi

info "Static analysis passed: codespell, bandit, vulture, ruff check, ruff format, ty"
