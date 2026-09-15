#!/usr/bin/env bash
# run-lint-gates.sh - this repo's lint gate: the hub lint aggregator over the
# tracked tree, every gate running even after one fails, with the verdict
# raised once at the end.
#
# A wrapper around ANTfrastructure's linux/scripts/run-lint-gates.sh, which owns
# the six gates (listed in its header), their pinned + SHA-verified bootstraps,
# the git-ls-files scope construction, the empty-scope vacuity guards and the
# gitleaks self-test (an empty tree must scan clean, a planted PAT must be
# reported at the path that was passed in - otherwise "no findings" cannot be
# told apart from "the scanner never started").
#
# THIS CLOSES A GAP, IT DOES NOT REPLACE ANYTHING. Before this file, the whole
# repo had no shell, workflow or secret linting at all: grepping the tree for
# the lint tool names found exactly one hit, and it was a disable directive
# (SC1090) inside lib/antfrastructure.sh - a suppression for a linter that never
# ran.
#
# The roundabout phrasing above is not accidental: a comment line whose FIRST
# word is the linter's own name parses as a DIRECTIVE, and one it cannot parse
# is itself an SC1073/SC1072 error. That is how this gate failed on its own
# wrapper the first time it ran - twice, because the explanation tripped it too.
#
# The consumer root is passed EXPLICITLY. Upstream refuses to infer it, and
# must: this script's hub half lives inside third_party/ANTfrastructure, so a
# root derived from its own location would grade ANTfrastructure's tree and report
# green over the wrong repo.
#
# --exclude defaults to third_party upstream, which is what this repo wants:
# third_party/ANTfrastructure is a submodule graded in its own repository at its
# own ratchet.
#
# --ratchets is passed UNCONDITIONALLY below, so the dev-box command and CI
# grade the same set. It adds the nine --root measurement gates (docs
# cross-references, code size, complexity, dead functions, comment size, stdout
# returns, masked declarations, trailing conditionals, the shellcheck warning
# ratchet) over this tree, reading the freeze files at the repo root:
# function-size.allow, file-size.allow, code-complexity.allow,
# comment-size.allow and dead-functions.allow. Those were seeded from the first
# run on 2026-09-15 and committed; the other four gates start empty because this
# tree has nothing to freeze for them. Each freeze file's header says what its
# rows are and what makes a row go stale.
#
#   scripts/linux/run-lint-gates.sh                     # the whole repo
#   scripts/linux/run-lint-gates.sh --exclude <dir>     # additional exclusions
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

antfrastructure_exec "linux/scripts/run-lint-gates.sh" "$KATAGLYPHIS_REPO_ROOT" --ratchets "$@"
