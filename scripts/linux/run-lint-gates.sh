#!/usr/bin/env bash
# Passes the repo root (upstream must not infer it) and --ratchets always, so the dev box grades what CI grades.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# Matches lint-gates.yml's `workflow-conventions: sha-pin`.
export WORKFLOW_CONVENTIONS_GATE="${WORKFLOW_CONVENTIONS_GATE:-sha-pin}"

antfrastructure_exec "linux/scripts/run-lint-gates.sh" "$KATAGLYPHIS_REPO_ROOT" --ratchets "$@"
