#!/usr/bin/env bash
# Prints the family CI image ref ([--windows]) and nothing else on stdout, so $(...) is safe; see AGENTS.md § 5.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

antfrastructure_exec "linux/scripts/ci-image-ref.sh" "$@"
