#!/usr/bin/env bash
# ci_build_docs.sh - project wrapper around ContainerHub's generic Python
# documentation builder (linux/scripts/02-toolchain/python/ci_build_docs.sh).
#
# The local copy also carried `if [ -f "$WORKSPACE_ROOT/flutter/bin:$PATH" ]`,
# which tests for a FILE whose name is a path with $PATH appended - it can never
# be true, and this is a Python project with no Flutter step. Dropped rather
# than ported.
set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DRIVER="${_SCRIPT_DIR}/../../ExternalLib/Kataglyphis-ContainerHub/linux/scripts/02-toolchain/python/ci_build_docs.sh"
[ -f "$_DRIVER" ] || { echo "Error: ContainerHub driver not found at $_DRIVER. Run: git submodule update --init --recursive ExternalLib/Kataglyphis-ContainerHub" >&2; exit 1; }

exec bash "$_DRIVER" "$@"
