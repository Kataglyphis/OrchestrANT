#!/usr/bin/env bash
# ci_static_analysis.sh - project wrapper around ContainerHub's generic Python
# static-analysis runner (linux/scripts/02-toolchain/python/ci_static_analysis.sh).
#
# That driver owns the whole pipeline: venv lifecycle via uv_venv_ensure,
# codespell/bandit/vulture/ruff/ty, and cleanup of a venv it created. This file
# previously reimplemented it - same tools, same order, ~48 lines - with the
# package name hard-coded. Upstream derives it from pyproject.toml instead.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/containerhub.sh"

# PACKAGE_NAME is set explicitly rather than left to upstream's
# derive_package_name: that reads the DISTRIBUTION name from pyproject.toml
# ("OrchestrANT"), but bandit/ruff/vulture/pytest --cov all want the
# importable MODULE directory, which is "orchestrant". The two differ in
# this project, so deriving would point every tool at a path that does not exist.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestrant}"

containerhub_exec "linux/scripts/02-toolchain/python/ci_static_analysis.sh" "$@"
