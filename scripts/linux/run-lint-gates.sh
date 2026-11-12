#!/usr/bin/env bash
# Passes the repo root (upstream must not infer it) and --ratchets always, so the dev box grades what CI grades.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# Matches lint-gates.yml's `workflow-conventions: sha-pin`.
export WORKFLOW_CONVENTIONS_GATE="${WORKFLOW_CONVENTIONS_GATE:-sha-pin}"

# First, and in a second: a refactor that moves a line a mutation recipe names fails here, not in CI after the push.
stale_check="$(antfrastructure_path docs/scripts/verify_mutations.py)"
for manifest in "${KATAGLYPHIS_REPO_ROOT}"/scripts/linux/tests/*mutations*.json; do
  python3 "${stale_check}" --manifest "${manifest}" --root "${KATAGLYPHIS_REPO_ROOT}" --stale-check
done

antfrastructure_exec "linux/scripts/run-lint-gates.sh" "$KATAGLYPHIS_REPO_ROOT" --ratchets --agents-drift "$@"
