#!/usr/bin/env bash
# ci_tests.sh - project wrapper around ContainerHub's generic Python CI test
# runner (linux/scripts/02-toolchain/python/ci_tests.sh).
#
# The driver owns the per-version venv matrix, the stable/experimental split,
# pytest + coverage flags and the tee'd log under docs/test_results/. The
# experimental-sync guard this repo had added locally was upstreamed with this
# change, so nothing is lost by delegating.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/containerhub.sh"

# PACKAGE_NAME is set explicitly rather than left to upstream's
# derive_package_name: that reads the DISTRIBUTION name from pyproject.toml
# ("Orchestr-ANT-ion"), but bandit/ruff/vulture/pytest --cov all want the
# importable MODULE directory, which is "orchestr_ant_ion". The two differ in
# this project, so deriving would point every tool at a path that does not exist.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestr_ant_ion}"

containerhub_exec "linux/scripts/02-toolchain/python/ci_tests.sh" "$@"
