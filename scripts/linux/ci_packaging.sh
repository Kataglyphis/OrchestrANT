#!/usr/bin/env bash
# Thin wrapper: change behaviour upstream in the hub's linux/scripts/02-toolchain/python/ci_packaging.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/antfrastructure.sh"

# The packages ship the model, which no checkout tracks: fetched by checksum before the hub copies app.json's data.
fetcher="$(antfrastructure_path linux/scripts/lib/fetch-data-lock.py)"
python3 -I "${fetcher}" --lock "${KATAGLYPHIS_REPO_ROOT}/resources/models/models.lock"

# A subshell, so the exec returns here: the hub's proof loads the cp314t modules, the next step runs them threaded.
( antfrastructure_exec "linux/scripts/02-toolchain/python/ci_packaging.sh" "$@" )

# The thread-safety tests on the installed cp314t wheel; ORCHESTRANT_REQUIRE_COMPILED fails a module that came from source.
antfrastructure_exec "linux/scripts/02-toolchain/python/test-free-threaded-wheel.sh" \
  ${1:+--python-version "$1"} --extra test --copy tests/unit/test_thread_safety.py \
  --env ORCHESTRANT_REQUIRE_COMPILED=1 --isolated -- -W error -W ignore::DeprecationWarning -v tests/unit/test_thread_safety.py
