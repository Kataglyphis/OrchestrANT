#!/usr/bin/env bash
# Proves the packaged ModelANT texts match their locked sources, not other dependency licences.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/antfrastructure.sh"
if [ "$#" -ne 1 ]; then
  printf 'Usage: %s PACKAGE_DIRECTORY\n' "$0" >&2
  exit 2
fi
package_dir="$(realpath -e -- "$1")"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
fetcher="$(antfrastructure_path linux/scripts/lib/fetch-data-lock.py)"
verifier="$(antfrastructure_path linux/scripts/verify_package_license.py)"
appimage_verifier="$(antfrastructure_path linux/scripts/06-packaging/verify-native-appimage-license.sh)"
python3 -I "$fetcher" --lock "${KATAGLYPHIS_REPO_ROOT}/resources/models/models.lock" --dest "$tmp" \
  --only deim_dfine_n_coco.LICENSE --only deim_dfine_n_coco.NOTICE
shopt -s nullglob
deb=("$package_dir"/*.deb)
tarball=("$package_dir"/*.tar.gz)
appimage=("$package_dir"/*.AppImage)
if [ "${#deb[@]}" -ne 1 ] || [ "${#tarball[@]}" -ne 1 ] || [ "${#appimage[@]}" -ne 1 ]; then
  printf 'Expected exactly one DEB, TAR and AppImage in %s\n' "$package_dir" >&2
  exit 1
fi
for name in deim_dfine_n_coco.LICENSE deim_dfine_n_coco.NOTICE; do
  python3 "$verifier" "${deb[0]}" --require "opt/orchestrant/share/orchestrant/models/${name}=${tmp}/${name}"
  python3 "$verifier" "${tarball[0]}" --require "*/share/orchestrant/models/${name}=${tmp}/${name}"
  bash "$appimage_verifier" "${appimage[0]}" "$(uname -m)" "usr/lib/orchestrant/share/orchestrant/models/${name}" "${tmp}/${name}"
done
