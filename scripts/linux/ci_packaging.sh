#!/usr/bin/env bash
# Thin wrapper: change behaviour upstream in the hub's linux/scripts/02-toolchain/python/ci_packaging.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/antfrastructure.sh"

# A subshell, so the exec returns here: the hub's proof loads the cp314t modules, the next step runs them threaded.
( antfrastructure_exec "linux/scripts/02-toolchain/python/ci_packaging.sh" "$@" )

bash "${SCRIPT_DIR}/ci_free_threaded_wheel_tests.sh" "$@"
