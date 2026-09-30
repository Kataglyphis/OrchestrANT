#!/usr/bin/env bash
# Passes the repo root itself, so do not pass one; see third_party/ANTfrastructure/docs/dependency-updates.md.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

antfrastructure_exec "linux/scripts/renovate-local.sh" "$KATAGLYPHIS_REPO_ROOT" "$@"
