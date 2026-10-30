#!/usr/bin/env bash
# Thin wrapper: change behaviour upstream in the hub's linux/scripts/02-toolchain/python/ci_tests.sh.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# Upstream derives it from the distribution name (OrchestrANT), which is not the module directory.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestrant}"
# The image carries the GenAI wheel, so the genai suites must run rather than skip; see tests/unit/genai/conftest.py.
export ORCHESTRANT_REQUIRE_GENAI="${ORCHESTRANT_REQUIRE_GENAI-1}"

antfrastructure_exec "linux/scripts/02-toolchain/python/ci_tests.sh" "$@"
