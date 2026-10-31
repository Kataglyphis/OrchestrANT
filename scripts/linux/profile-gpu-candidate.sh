#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo 'usage: profile-gpu-candidate.sh ARTIFACT_DIR nvidia|amd x64|arm64 [--print-archive]' >&2
  exit 2
fi
artifact_dir=$(realpath -e "$1") vendor=$2 arch=$3 mode=${4:-}
case "$vendor:$arch" in
  nvidia:x64) machine=x86_64; debarch=amd64; device=cuda ;;
  nvidia:arm64) machine=aarch64; debarch=arm64; device=cuda ;;
  amd:x64) machine=x86_64; debarch=amd64; device=migraphx ;;
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
debs=("$artifact_dir"/packages/orchestrant-gpu-"$vendor"_*_"$debarch".deb)
appimages=("$artifact_dir"/packages/orchestrant-gpu-"$vendor"-*-"$machine".AppImage)
[[ ${#debs[@]} -eq 1 && ${#appimages[@]} -eq 1 ]] || {
  echo "Expected exactly one $vendor $arch DEB and AppImage" >&2; exit 1;
}
command -v dpkg-deb >/dev/null || { echo 'dpkg-deb is required' >&2; exit 1; }
# Keep host SDK library paths, but prevent unrelated injected libraries from affecting the proof.
unset LD_PRELOAD LD_AUDIT PYTHONPATH PYTHONHOME
stage="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/orchestrant-profile.XXXXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
stage=$(realpath -e "$stage")
mkdir "$stage/tar" "$stage/deb" "$stage/appimage-tmp"
tar -xzf "${archives[0]}" -C "$stage/tar"
mapfile -t roots < <(find "$stage/tar" -mindepth 1 -maxdepth 1 -type d)
[ "${#roots[@]}" -eq 1 ] || { echo 'Expected exactly one candidate bundle' >&2; exit 1; }
echo 'Profiling the TAR bundle'
"${roots[0]}/bin/orchestrant-smoke" --ort-profile "$device" --verify-default
rm -rf -- "$stage/tar"
dpkg-deb -x "${debs[0]}" "$stage/deb"
echo 'Profiling the DEB payload'
"$stage/deb/opt/orchestrant/bin/orchestrant-smoke" --ort-profile "$device" --verify-default
rm -rf -- "$stage/deb"
# Artifact downloads do not preserve executable bits. AppRun dispatches by the alias name.
cp -- "${appimages[0]}" "$stage/candidate.AppImage"
chmod u+x "$stage/candidate.AppImage"
ln -s candidate.AppImage "$stage/orchestrant-smoke"
echo 'Profiling the finished AppImage'
APPIMAGE_EXTRACT_AND_RUN=1 TMPDIR="$stage/appimage-tmp" \
  "$stage/orchestrant-smoke" --ort-profile "$device" --verify-default
