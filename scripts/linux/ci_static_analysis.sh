#!/usr/bin/env bash
# ci_static_analysis.sh - project wrapper around ContainerHub's Python
# static-analysis GATE (linux/scripts/02-toolchain/python/ci_static_analysis.sh).
#
# WHY THIS IS A WRAPPER AGAIN
# ---------------------------
# It was a 131-line local fork, and the fork existed for exactly one reason:
# the upstream driver ended EVERY tool line with `|| true` (four of them also
# with `2>/dev/null`), so the "Python static analysis" step of the reusable
# Linux lane could not fail. Measured on this tree: ruff alone had 63 findings
# while the step was green.
#
# Upstream now gates. Verified against the ContainerHub working tree before
# collapsing this file, in the order this file's previous header demanded:
#   * the six `|| true` and the four `2>/dev/null` are gone; the six tools run
#     through 01-core/gates.sh's run_gate and the verdict is raised once by
#     assert_gates at the bottom (which also fails when NO gate ran, so an
#     empty batch cannot report green);
#   * `ruff check --no-fix`, not `--fix` - the gate judges the tree as
#     committed instead of repairing a checkout CI throws away;
#   * `ruff format --check --diff`, not a bare `format` that rewrites and
#     always exits 0;
#   * the tool list, the target list (`$PACKAGE_NAME tests docs/source/conf.py
#     setup.py`, plus README.md for codespell) and bandit's -x exclusion string
#     are the ones this fork used, character for character.
# The local `run_gate`/`GATE_FAILURES` accumulator this file carried is the
# thing that became 01-core/gates.sh, so delegating loses nothing of it.
#
# ONE BEHAVIOURAL DELTA, RECORDED RATHER THAN HIDDEN: the upstream driver still
# writes `git config --global --add safe.directory "$WORKSPACE_ROOT" || true`,
# where this fork deliberately dropped the `|| true`. If git cannot mark the
# workspace safe, every tool that shells out to git is about to misbehave in a
# way much harder to read than that failure. That is a ContainerHub line and
# belongs to ContainerHub; it is reported upstream, not re-forked here.
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

containerhub_exec "linux/scripts/02-toolchain/python/ci_static_analysis.sh" "$@"
