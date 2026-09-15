#!/usr/bin/env bash
# renovate-local.sh - what this repo's dependencies are behind on, decided by
# Renovate run as a LOCAL CLI. A wrapper around ANTfrastructure's
# linux/scripts/renovate-local.sh, which owns the pinned node/renovate
# bootstraps, the machine-readable report parse and the apply half.
#
#   scripts/linux/renovate-local.sh                    # report (default)
#   scripts/linux/renovate-local.sh --managers pep621  # this repo's pip side
#   scripts/linux/renovate-local.sh --apply --dry-run  # show the plan
#   scripts/linux/renovate-local.sh --apply            # move the gitlinks
#
# --apply moves gitlinks only for submodules that declare a `branch =` in
# .gitmodules; here that is third_party/ANTfrastructure (branch = main), the one
# submodule this repo has, so nothing else can come back REFUSED.
#
# The repo root is passed EXPLICITLY below, so do not pass one yourself: a
# trailing `.` gets "renovate-local.sh: more than one repo root given".
#
# Rationale, which git runs the apply half, and the RENOVATE_TOKEN variant:
# third_party/ANTfrastructure/docs/dependency-updates.md
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

antfrastructure_exec "linux/scripts/renovate-local.sh" "$KATAGLYPHIS_REPO_ROOT" "$@"
