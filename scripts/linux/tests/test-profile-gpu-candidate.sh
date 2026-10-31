#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
selector="$repo/scripts/linux/profile-gpu-candidate.sh"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/packages" "$fixture/staged/bin" "$fixture/deb/DEBIAN" "$fixture/deb/opt/orchestrant/bin"
export FAKE_SEEN="$fixture/seen" LD_LIBRARY_PATH=/host/gpu-sdk
cat > "$fixture/staged/bin/orchestrant-smoke" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 3 && "$1" == --ort-profile && "$3" == --verify-default ]]
[[ -z "${LD_PRELOAD+x}${LD_AUDIT+x}${PYTHONPATH+x}${PYTHONHOME+x}" ]]
[[ "$LD_LIBRARY_PATH" == /host/gpu-sdk ]]
case "$0" in
  */tar/*) format=tar ;;
  */deb/*) format=deb ;;
  *) exit 1 ;;
esac
printf '%s:%s\n' "$format" "$2" >> "$FAKE_SEEN"
[[ "${FAKE_FAIL:-}" != "$format" ]]
LAUNCHER
chmod +x "$fixture/staged/bin/orchestrant-smoke"
cp "$fixture/staged/bin/orchestrant-smoke" "$fixture/deb/opt/orchestrant/bin/orchestrant-smoke"
cat > "$fixture/deb/DEBIAN/control" <<'CONTROL'
Package: orchestrant-fixture
Version: 1.0
Architecture: all
Maintainer: Test <test@example.invalid>
Description: GPU profiling fixture
CONTROL
for row in 'nvidia x86_64 amd64' 'nvidia aarch64 arm64' 'amd x86_64 amd64'; do
  read -r vendor machine debarch <<< "$row"
  tar -C "$fixture" -czf "$fixture/packages/orchestrant-gpu-$vendor-1.0-linux-$machine.tar.gz" staged
  dpkg-deb --root-owner-group --build "$fixture/deb" "$fixture/packages/orchestrant-gpu-${vendor}_1.0_${debarch}.deb" >/dev/null
  cat > "$fixture/packages/orchestrant-gpu-$vendor-1.0-$machine.AppImage" <<'APPIMAGE'
#!/usr/bin/env bash
set -euo pipefail
[[ "${0##*/}" == orchestrant-smoke && "${APPIMAGE_EXTRACT_AND_RUN:-}" == 1 ]]
[[ $# -eq 3 && "$1" == --ort-profile && "$3" == --verify-default ]]
[[ -z "${LD_PRELOAD+x}${LD_AUDIT+x}${PYTHONPATH+x}${PYTHONHOME+x}" ]]
[[ "$LD_LIBRARY_PATH" == /host/gpu-sdk && -d "$TMPDIR" ]]
printf 'appimage:%s\n' "$2" >> "$FAKE_SEEN"
[[ "${FAKE_FAIL:-}" != appimage ]]
APPIMAGE
  # Actions artifact downloads discard executable permissions.
  chmod 0644 "$fixture/packages/orchestrant-gpu-$vendor-1.0-$machine.AppImage"
done
[[ "$(bash "$selector" "$fixture" nvidia x64 --print-archive)" == "$fixture/packages/orchestrant-gpu-nvidia-1.0-linux-x86_64.tar.gz" ]]
[[ "$(bash "$selector" "$fixture" nvidia arm64 --print-archive)" == "$fixture/packages/orchestrant-gpu-nvidia-1.0-linux-aarch64.tar.gz" ]]
[[ "$(bash "$selector" "$fixture" amd x64 --print-archive)" == "$fixture/packages/orchestrant-gpu-amd-1.0-linux-x86_64.tar.gz" ]]
for row in 'nvidia x64 cuda' 'nvidia arm64 cuda' 'amd x64 migraphx'; do
  read -r vendor arch device <<< "$row"
  : > "$FAKE_SEEN"
  RUNNER_TEMP="$fixture" PYTHONPATH=/wrong PYTHONHOME=/wrong bash "$selector" "$fixture" "$vendor" "$arch"
  printf 'tar:%s\ndeb:%s\nappimage:%s\n' "$device" "$device" "$device" > "$fixture/expected"
  cmp "$fixture/expected" "$FAKE_SEEN"
  for format in tar deb appimage; do
    if FAKE_FAIL="$format" RUNNER_TEMP="$fixture" bash "$selector" "$fixture" "$vendor" "$arch" >/dev/null 2>&1; then
      echo "$format failure was accepted for $vendor $arch" >&2; exit 1
    fi
  done
done
if bash "$selector" "$fixture" amd arm64 --print-archive >/dev/null 2>&1; then exit 1; fi
mv "$fixture/packages/orchestrant-gpu-nvidia_1.0_amd64.deb" "$fixture/missing.deb"
if bash "$selector" "$fixture" nvidia x64 >/dev/null 2>&1; then echo 'Missing DEB was accepted' >&2; exit 1; fi
mv "$fixture/missing.deb" "$fixture/packages/orchestrant-gpu-nvidia_1.0_amd64.deb"
cp "$fixture/packages/orchestrant-gpu-nvidia-1.0-x86_64.AppImage" "$fixture/packages/orchestrant-gpu-nvidia-2.0-x86_64.AppImage"
if bash "$selector" "$fixture" nvidia x64 >/dev/null 2>&1; then echo 'Duplicate AppImage was accepted' >&2; exit 1; fi
touch "$fixture/packages/orchestrant-gpu-nvidia-2.0-linux-x86_64.tar.gz"
if bash "$selector" "$fixture" nvidia x64 --print-archive >/dev/null 2>&1; then exit 1; fi
rm "$fixture/packages/orchestrant-gpu-nvidia-1.0-linux-aarch64.tar.gz"
if bash "$selector" "$fixture" nvidia arm64 --print-archive >/dev/null 2>&1; then exit 1; fi
echo 'GPU candidate package profiling fixtures passed'
