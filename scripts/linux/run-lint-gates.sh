#!/usr/bin/env bash
# Passes the repo root (upstream must not infer it) and --ratchets always, so the dev box grades what CI grades.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

antfrastructure_exec "linux/scripts/run-lint-gates.sh" "$KATAGLYPHIS_REPO_ROOT" --ratchets "$@"
