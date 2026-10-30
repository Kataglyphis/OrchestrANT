#!/usr/bin/env bash
# Proves the DEB, tar and AppImage ship the locked model texts, the third-party notice and its supplied texts, and the logo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/antfrastructure.sh"
if [ "$#" -ne 1 ]; then
  printf 'Usage: %s PACKAGE_DIRECTORY\n' "$0" >&2
  exit 2
fi
package_dir="$(realpath -e -- "$1")"
repo="${KATAGLYPHIS_REPO_ROOT}"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
fetcher="$(antfrastructure_path linux/scripts/lib/fetch-data-lock.py)"
verifier="$(antfrastructure_path linux/scripts/verify_package_license.py)"
appimage_verifier="$(antfrastructure_path linux/scripts/06-packaging/verify-native-appimage-license.sh)"
python3 -I "$fetcher" --lock "${repo}/resources/models/models.lock" --dest "$tmp" \
  --only deim_dfine_n_coco.LICENSE --only deim_dfine_n_coco.NOTICE
shopt -s nullglob
deb=("$package_dir"/*.deb)
tarball=("$package_dir"/*.tar.gz)
appimage=("$package_dir"/*.AppImage)
if [ "${#deb[@]}" -ne 1 ] || [ "${#tarball[@]}" -ne 1 ] || [ "${#appimage[@]}" -ne 1 ]; then
  printf 'Expected exactly one DEB, TAR and AppImage in %s\n' "$package_dir" >&2
  exit 1
fi
# Bundle-relative path and source of every text the packages must carry byte for byte.
texts=()
for name in deim_dfine_n_coco.LICENSE deim_dfine_n_coco.NOTICE; do
  texts+=("share/orchestrant/models/${name}=${tmp}/${name}")
done
texts+=("THIRD-PARTY-NOTICES.txt=${repo}/packaging/THIRD-PARTY-NOTICES.txt")
for supplied in "${repo}"/packaging/licenses/* "${repo}"/packaging/native-licenses/*; do
  texts+=("licenses/${supplied##*/}=${supplied}")
done
deb_rules=(--require "usr/share/icons/hicolor/256x256/apps/orchestrant.png=${repo}/images/logo.png")
tar_rules=()
for text in "${texts[@]}"; do
  deb_rules+=(--require "opt/orchestrant/${text}")
  tar_rules+=(--require "*/${text}")
  bash "$appimage_verifier" "${appimage[0]}" "$(uname -m)" "usr/lib/orchestrant/${text%%=*}" "${text#*=}"
done
python3 "$verifier" "${deb[0]}" "${deb_rules[@]}"
python3 "$verifier" "${tarball[0]}" "${tar_rules[@]}"
bash "$appimage_verifier" "${appimage[0]}" "$(uname -m)" "orchestrant.png" "${repo}/images/logo.png"
python3 -I "${repo}/scripts/third_party_notices.py" check "${tarball[0]}"
