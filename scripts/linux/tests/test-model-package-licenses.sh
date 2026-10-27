#!/usr/bin/env bash
# Checks Linux package model-license proofs with local fixtures, not real AppImage runtimes.
set -u
tests_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tests_dir/../../.." && pwd)"
hub="${ANTFRASTRUCTURE_DIR:-$repo/third_party/ANTfrastructure}"
source "$hub/linux/scripts/tests/test-harness.sh"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
fixture="$work/fixture"
model_dir="share/orchestrant/models"
mkdir -p "$fixture/resources/models" "$work/original" "$work/files/$model_dir" "$work/packages"
for name in deim_dfine_n_coco.LICENSE deim_dfine_n_coco.NOTICE; do
  printf 'original %s\n' "$name" > "$work/original/$name"
  cp "$work/original/$name" "$work/files/$model_dir/$name"
  printf '%s  %s  file:%s\n' "$(sha256sum "$work/original/$name" | cut -d' ' -f1)" "$name" "$work/original/$name" >> "$fixture/resources/models/models.lock"
done
mkdir -p "$work/deb/DEBIAN" "$work/deb/opt/orchestrant/share/orchestrant"
printf 'Package: orchestrant\nVersion: 1\nArchitecture: all\nMaintainer: Test <test@example.invalid>\nDescription: Fixture\n' > "$work/deb/DEBIAN/control"
cp -a "$work/files/$model_dir" "$work/deb/opt/orchestrant/share/orchestrant/"
dpkg-deb --root-owner-group --build "$work/deb" "$work/packages/test.deb" >/dev/null
mkdir -p "$work/tar/app"
cp -a "$work/files/share" "$work/tar/app/"
tar -C "$work/tar" -czf "$work/packages/test.tar.gz" app
mkdir -p "$work/app/usr/lib/orchestrant/share/orchestrant"
cp -a "$work/files/$model_dir" "$work/app/usr/lib/orchestrant/share/orchestrant/"
cat > "$work/packages/test.AppImage" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p "squashfs-root/$(dirname "$2")"
cp "$APPIMAGE_FIXTURE_ROOT/$2" "squashfs-root/$2"
SH
chmod +x "$work/packages/test.AppImage"
export ANTFRASTRUCTURE_DIR="$hub" KATAGLYPHIS_REPO_ROOT="$fixture" KATAGLYPHIS_MODEL_CACHE="$work/cache" APPIMAGE_FIXTURE_ROOT="$work/app"
check() { bash "$repo/scripts/linux/check-model-package-licenses.sh" "$work/packages"; }

t_case 'all three artifact types carry both locked original texts'
t_assert_ok check
t_case 'a missing TAR notice fails the proof'
rm "$work/tar/app/$model_dir/deim_dfine_n_coco.NOTICE"
tar -C "$work/tar" -czf "$work/packages/test.tar.gz" app
t_assert_fails check
cp "$work/original/deim_dfine_n_coco.NOTICE" "$work/tar/app/$model_dir/"
tar -C "$work/tar" -czf "$work/packages/test.tar.gz" app
t_case 'a missing DEB licence fails the proof'
rm "$work/deb/opt/orchestrant/$model_dir/deim_dfine_n_coco.LICENSE"
dpkg-deb --root-owner-group --build "$work/deb" "$work/packages/test.deb" >/dev/null
t_assert_fails check
cp "$work/original/deim_dfine_n_coco.LICENSE" "$work/deb/opt/orchestrant/$model_dir/"
dpkg-deb --root-owner-group --build "$work/deb" "$work/packages/test.deb" >/dev/null
t_case 'a missing AppImage notice fails the proof'
rm "$work/app/usr/lib/orchestrant/$model_dir/deim_dfine_n_coco.NOTICE"
t_assert_fails check
t_case 'missing artifact fails rather than skipping its proof'
rm "$work/packages/test.AppImage"
t_assert_fails check
t_summary
