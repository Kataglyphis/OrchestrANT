#!/usr/bin/env bash
# Checks the Linux package licence proofs with local fixtures, not real AppImage runtimes or real bundles.
set -u
tests_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tests_dir/../../.." && pwd)"
hub="${ANTFRASTRUCTURE_DIR:-$repo/third_party/ANTfrastructure}"
source "$hub/linux/scripts/tests/test-harness.sh"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
fixture="$work/fixture"
model_dir="share/orchestrant/models"
mkdir -p "$fixture/resources/models" "$fixture/scripts" "$fixture/packaging/licenses" "$fixture/images" \
  "$work/original" "$work/files/$model_dir" "$work/files/licenses" "$work/packages"
cp "$repo/scripts/third_party_notices.py" "$fixture/scripts/"
cp "$repo/images/logo.png" "$fixture/images/logo.png"
for name in deim_dfine_n_coco.LICENSE deim_dfine_n_coco.NOTICE; do
  printf 'original %s\n' "$name" > "$work/original/$name"
  cp "$work/original/$name" "$work/files/$model_dir/$name"
  printf '%s  %s  file:%s\n' "$(sha256sum "$work/original/$name" | cut -d' ' -f1)" "$name" "$work/original/$name" >> "$fixture/resources/models/models.lock"
done
printf 'Python distributions\n====\n\nNative components outside site-packages\n====\n' > "$fixture/packaging/THIRD-PARTY-NOTICES.txt"
cp "$fixture/packaging/THIRD-PARTY-NOTICES.txt" "$work/files/"
printf 'supplied wheel licence\n' > "$fixture/packaging/licenses/example-LICENSE"
cp "$fixture/packaging/licenses/example-LICENSE" "$work/files/licenses/"
build_deb() {
  rm -rf "$work/deb"
  mkdir -p "$work/deb/DEBIAN" "$work/deb/opt/orchestrant" "$work/deb/usr/share/icons/hicolor/256x256/apps"
  printf 'Package: orchestrant\nVersion: 1\nArchitecture: all\nMaintainer: Test <test@example.invalid>\nDescription: Fixture\n' > "$work/deb/DEBIAN/control"
  cp -a "$work/files/." "$work/deb/opt/orchestrant/"
  cp "$fixture/images/logo.png" "$work/deb/usr/share/icons/hicolor/256x256/apps/orchestrant.png"
  dpkg-deb --root-owner-group --build "$work/deb" "$work/packages/orchestrant_1_amd64.deb" >/dev/null
}
build_tar() {
  rm -rf "$work/tar"
  mkdir -p "$work/tar/app"
  cp -a "$work/files/." "$work/tar/app/"
  tar -C "$work/tar" -czf "$work/packages/orchestrant-1-linux-x86_64.tar.gz" app
}
build_appimage() {
  rm -rf "$work/app"
  mkdir -p "$work/app/usr/lib/orchestrant"
  cp -a "$work/files/." "$work/app/usr/lib/orchestrant/"
  cp "$fixture/images/logo.png" "$work/app/orchestrant.png"
}
build_deb
build_tar
build_appimage
cat > "$work/packages/orchestrant-1-x86_64.AppImage" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p "squashfs-root/$(dirname "$2")"
cp "$APPIMAGE_FIXTURE_ROOT/$2" "squashfs-root/$2"
SH
chmod +x "$work/packages/orchestrant-1-x86_64.AppImage"
export ANTFRASTRUCTURE_DIR="$hub" KATAGLYPHIS_REPO_ROOT="$fixture" KATAGLYPHIS_MODEL_CACHE="$work/cache" APPIMAGE_FIXTURE_ROOT="$work/app"
check() { bash "$repo/scripts/linux/check-package-licenses.sh" "$work/packages"; }

t_case 'all three artifact types carry the model texts, the notice, the supplied texts and the logo'
t_assert_ok check
t_case 'a missing TAR model notice fails the proof'
rm "$work/files/$model_dir/deim_dfine_n_coco.NOTICE"
build_tar
t_assert_fails check
cp "$work/original/deim_dfine_n_coco.NOTICE" "$work/files/$model_dir/"
build_tar
t_case 'a missing DEB model licence fails the proof'
rm "$work/files/$model_dir/deim_dfine_n_coco.LICENSE"
build_deb
t_assert_fails check
cp "$work/original/deim_dfine_n_coco.LICENSE" "$work/files/$model_dir/"
build_deb
t_case 'a DEB without the third-party notice fails the proof'
rm "$work/deb/opt/orchestrant/THIRD-PARTY-NOTICES.txt"
dpkg-deb --root-owner-group --build "$work/deb" "$work/packages/orchestrant_1_amd64.deb" >/dev/null
t_assert_fails check
build_deb
t_case 'a TAR without a supplied wheel licence fails the proof'
rm "$work/tar/app/licenses/example-LICENSE"
tar -C "$work/tar" -czf "$work/packages/orchestrant-1-linux-x86_64.tar.gz" app
t_assert_fails check
build_tar
t_case 'a DEB whose desktop icon is not the logo fails the proof'
printf 'placeholder' > "$work/deb/usr/share/icons/hicolor/256x256/apps/orchestrant.png"
dpkg-deb --root-owner-group --build "$work/deb" "$work/packages/orchestrant_1_amd64.deb" >/dev/null
t_assert_fails check
build_deb
t_case 'an AppImage whose icon is not the logo fails the proof'
printf 'placeholder' > "$work/app/orchestrant.png"
t_assert_fails check
build_appimage
t_case 'a TAR with a native library the notice does not list fails the proof'
mkdir -p "$work/tar/app/runtime/lib"
printf '\177ELF fixture' > "$work/tar/app/runtime/lib/libunlisted.so.1"
tar -C "$work/tar" -czf "$work/packages/orchestrant-1-linux-x86_64.tar.gz" app
t_assert_fails check
build_tar
t_case 'the restored fixture passes again'
t_assert_ok check
t_case 'a missing AppImage model notice fails the proof'
rm "$work/app/usr/lib/orchestrant/$model_dir/deim_dfine_n_coco.NOTICE"
t_assert_fails check
t_case 'missing artifact fails rather than skipping its proof'
rm "$work/packages/orchestrant-1-x86_64.AppImage"
t_assert_fails check
t_summary
