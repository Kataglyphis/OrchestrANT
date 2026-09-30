#!/usr/bin/env bash
# Thin wrapper: change behaviour upstream in the hub's linux/scripts/02-toolchain/python/ci_tests.sh.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# Upstream derives it from the distribution name (OrchestrANT), which is not the module directory.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestrant}"

antfrastructure_exec "linux/scripts/02-toolchain/python/ci_tests.sh" "$@"
