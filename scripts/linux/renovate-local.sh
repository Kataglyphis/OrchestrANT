#!/usr/bin/env bash
# renovate-local.sh - what this repo's dependencies are behind on, decided by
# Renovate run as a LOCAL CLI.
#
# A wrapper around ContainerHub's linux/scripts/renovate-local.sh, which owns
# the bootstraps - RENOVATE_NODE_VERSION (checksum-verified against upstream's
# SHASUMS256) and RENOVATE_VERSION, both pinned in the hub's versions.env and
# deliberately NOT the canonical NODE_VERSION, since Renovate 44 declares
# engines.node "^24.11.0" while the images ship 26 - plus the machine-readable
# report parse and the apply half. Owner directive 2026-09-09: dependency
# upgrades in this family go through this tool rather than by hand.
#
#   scripts/linux/renovate-local.sh                    # report (default)
#   scripts/linux/renovate-local.sh --apply --dry-run  # show the plan
#   scripts/linux/renovate-local.sh --apply            # move the gitlinks
#   scripts/linux/renovate-local.sh --managers pep621  # this repo's pip side
#
# The repo root is passed EXPLICITLY, so the report grades THIS repo from any
# working directory - which also means you must not pass one yourself: a
# trailing `.` gets "renovate-local.sh: more than one repo root given".
#
# NOTHING RUNS THIS FOR YOU. The Renovate GitHub App is installed on no repo in
# this family, so this CLI is the only thing that ever reads the tracked
# .github/renovate.json (.github/dependabot.yml is GitHub's own machinery and
# unrelated to both). No workflow calls this script; it blocks no commit.
#
# TWO HALVES, AND ONLY ONE OF THEM WRITES. Renovate's --platform=local forces
# dryRun: it DETECTS, and never edits a file. The upstream --apply half is git,
# and it moves GITLINKS only - explicit paths, only for submodules that declare
# a `branch =`. Here that is third_party/ContainerHub (branch = main), the one
# submodule this repo has and the one whose drift silently changes every gate.
#
# THE APPLY HALF NEEDS THE GIT THAT WROTE THE WORKING TREE. There is no node on
# this Windows host, so the report half runs from WSL - but a Linux git over a
# Windows checkout sees every text file as CR-modified and aborts part way
# through, leaving the superproject half updated. The upstream script handles
# that itself: it switches to git.exe when WSL can reach it, and refuses up
# front when it cannot. Nothing for you to do about it - but do not read "run
# it from WSL" as meaning the trap is not there.
#
# THE PIP SIDE IS REPORT-ONLY. The default manager is git-submodules. This repo
# declares dependencies in pyproject.toml plus uv.lock - no requirements*.txt,
# and no install_requires in setup.py - so the manager that reads them is
# `pep621`. Observed 2026-09-09 (`--managers pep621`, run from WSL): 9 rows,
# every one an `==` pin - ruff 0.16.4 -> 0.16.6, plus torch 2.13.0 and
# torchvision 0.28.0 once per pytorch extra. Nothing else was named. The
# unpinned declarations (mlflow, flask, ...) carry no version to bump, and the
# transitive pins in uv.lock - where this repo's open security alerts live -
# are not this manager's to report. Those move with `uv lock --upgrade`, which
# neither half of this script runs for you.
#
# Rationale, which git runs the apply half, and the RENOVATE_TOKEN variant:
# third_party/ContainerHub/docs/dependency-updates.md
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/containerhub.sh"

containerhub_exec "linux/scripts/renovate-local.sh" "$KATAGLYPHIS_REPO_ROOT" "$@"
