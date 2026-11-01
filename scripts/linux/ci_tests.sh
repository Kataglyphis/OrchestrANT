#!/usr/bin/env bash
# Thin wrapper: change behaviour upstream in the hub's linux/scripts/02-toolchain/python/ci_tests.sh.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# Upstream derives it from the distribution name (OrchestrANT), which is not the module directory.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestrant}"
# The image carries the GenAI wheel, so the genai suites must run rather than skip; see tests/unit/genai/conftest.py.
export ORCHESTRANT_REQUIRE_GENAI="${ORCHESTRANT_REQUIRE_GENAI-1}"

# tests/unit/genai/test_contract.py replays ModelANT's text-generation-v1 on these; riscv64 installs no GenAI to run it.
if [ "$(uname -m)" != riscv64 ]; then
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fetcher="$(antfrastructure_path linux/scripts/lib/fetch-data-lock.py)"
  python3 -I "${fetcher}" --lock "${repo}/resources/models/llm.lock" --extract
  python3 -I "${fetcher}" --lock "${repo}/scripts/linux/ai-regression.lock" --only text-generation-v1.json \
    --dest "${repo}/resources/models/contracts"
  export ORCHESTRANT_REQUIRE_MODEL="${ORCHESTRANT_REQUIRE_MODEL-1}"
fi

antfrastructure_exec "linux/scripts/02-toolchain/python/ci_tests.sh" "$@"
