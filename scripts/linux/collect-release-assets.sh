#!/usr/bin/env bash
# collect-release-assets.sh <artifacts dir> <out dir>: every lane's packages/, flat and uniquely named, for a GitHub release.
set -euo pipefail

artifacts="${1:?usage: collect-release-assets.sh <artifacts dir> <out dir>}"
out="${2:?usage: collect-release-assets.sh <artifacts dir> <out dir>}"
mkdir -p "$out"
# Absolute, so every step below means the same folder whatever its working directory.
out="$(cd "$out" && pwd)"

for dir in "$artifacts"/*/; do
  name="$(basename "$dir")"
  # Test bundles and reports are no deliverable.
  case "$name" in *-tests|*-test-results|test-reports-*) continue ;; esac
  # The packagers write the .deb, AppImage, tarball, MSI, MSIX, zip and test certificate under packages/; wheels stay out.
  while IFS= read -r -d '' file; do
    if [ -e "${out}/$(basename "$file")" ]; then
      printf 'two artifacts carry %s\n' "$(basename "$file")" >&2
      exit 1
    fi
    cp "$file" "${out}/"
  done < <(find "$dir" -type f -path '*/packages/*' -print0)
done

if [ -z "$(ls -A "$out")" ]; then
  printf 'no packages under %s\n' "$artifacts" >&2
  exit 1
fi
ls -1 "$out"
