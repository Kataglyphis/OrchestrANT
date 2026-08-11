#!/usr/bin/env bash
# ci_static_analysis.sh - project wrapper around ContainerHub's generic Python
# static-analysis runner (linux/scripts/02-toolchain/python/ci_static_analysis.sh).
#
# That driver owns the whole pipeline: venv lifecycle via uv_venv_ensure,
# codespell/bandit/vulture/ruff/ty, and cleanup of a venv it created. This file
# previously reimplemented it - same tools, same order, ~48 lines - with the
# package name hard-coded. Upstream derives it from pyproject.toml instead.
set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DRIVER="${_SCRIPT_DIR}/../../ExternalLib/Kataglyphis-ContainerHub/linux/scripts/02-toolchain/python/ci_static_analysis.sh"
[ -f "$_DRIVER" ] || { echo "Error: ContainerHub driver not found at $_DRIVER. Run: git submodule update --init --recursive ExternalLib/Kataglyphis-ContainerHub" >&2; exit 1; }

# PACKAGE_NAME is set explicitly rather than left to upstream's
# derive_package_name: that reads the DISTRIBUTION name from pyproject.toml
# ("Orchestr-ANT-ion"), but bandit/ruff/vulture/pytest --cov all want the
# importable MODULE directory, which is "orchestr_ant_ion". The two differ in
# this project, so deriving would point every tool at a path that does not exist.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestr_ant_ion}"

# WORKSPACE_ROOT must be THIS repo, not the submodule. Upstream's
# detect_workspace derives it from the sourcing script's own location, which for
# a delegated driver is .../ExternalLib/Kataglyphis-ContainerHub/linux/scripts -
# so every tool would run against the submodule tree. detect_workspace honours a
# pre-set value, and still overrides to /workspace in the container, so CI is
# unaffected.
export WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "${_SCRIPT_DIR}/../.." && pwd)}"

exec bash "$_DRIVER" "$@"
