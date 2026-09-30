#!/usr/bin/env bash
# Thin wrapper: change behaviour upstream in the hub's linux/scripts/02-toolchain/python/ci_packaging.sh.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

antfrastructure_exec "linux/scripts/02-toolchain/python/ci_packaging.sh" "$@"
