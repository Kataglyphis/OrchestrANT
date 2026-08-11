#!/usr/bin/env bash
# ci_build_docs.sh - project wrapper around ContainerHub's generic Python
# documentation builder (linux/scripts/02-toolchain/python/ci_build_docs.sh).
#
# The local copy also carried `if [ -f "$WORKSPACE_ROOT/flutter/bin:$PATH" ]`,
# which tests for a FILE whose name is a path with $PATH appended - it can never
# be true, and this is a Python project with no Flutter step. Dropped rather
# than ported.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/containerhub.sh"

containerhub_exec "linux/scripts/02-toolchain/python/ci_build_docs.sh" "$@"
