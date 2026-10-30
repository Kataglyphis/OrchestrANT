#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo 'usage: profile-gpu-candidate.sh ARTIFACT_DIR nvidia|amd x64|arm64 [--print-archive]' >&2
  exit 2
fi
artifact_dir=$1 vendor=$2 arch=$3 mode=${4:-}
case "$vendor:$arch" in
  nvidia:x64) machine=x86_64; device=cuda ;;
  nvidia:arm64) machine=aarch64; device=cuda ;;
  amd:x64) machine=x86_64; device=migraphx ;;
  *) echo 'Unsupported GPU candidate' >&2; exit 2 ;;
esac
case "$mode" in ''|--print-archive) ;; *) echo 'Unknown mode' >&2; exit 2 ;; esac
shopt -s nullglob
archives=("$artifact_dir"/packages/orchestrant-gpu-"$vendor"-*-linux-"$machine".tar.gz)
if [ "${#archives[@]}" -ne 1 ]; then
  echo "Expected exactly one $vendor $arch candidate tarball in $artifact_dir/packages" >&2
  exit 1
fi
if [ "$mode" = --print-archive ]; then
  printf '%s\n' "${archives[0]}"
  exit 0
fi
stage="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/orchestrant-profile.XXXXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
tar -xzf "${archives[0]}" -C "$stage"
mapfile -t roots < <(find "$stage" -mindepth 1 -maxdepth 1 -type d)
[ "${#roots[@]}" -eq 1 ] || { echo 'Expected exactly one candidate bundle' >&2; exit 1; }
"${roots[0]}/bin/orchestrant-smoke" --ort-profile "$device"
