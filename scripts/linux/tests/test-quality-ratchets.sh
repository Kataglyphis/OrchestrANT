#!/usr/bin/env bash
# Grades this tree with the hub's size and complexity ratchets as lint-gates.yml does; the comment, dead-function and shellcheck ratchets are not run here.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
hub="${ANTFRASTRUCTURE_DIR:-$repo/third_party/ANTfrastructure}"
# A mutation mirror has no .git, and the gates grade tracked files only.
if [ ! -e "$repo/.git" ]; then
  git -C "$repo" init -q
  git -C "$repo" add -A -- . ':(exclude)third_party/ANTfrastructure'
fi
python3 "$hub/linux/scripts/verify_code_size.py" --root "$repo"
python3 "$hub/linux/scripts/verify_code_complexity.py" --root "$repo"
