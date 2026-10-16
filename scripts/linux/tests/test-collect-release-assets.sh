#!/usr/bin/env bash
# test-collect-release-assets.sh: collect-release-assets.sh against artifact trees shaped like the lanes' uploads.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
collect="${here}/../collect-release-assets.sh"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
fails=0
check() {
  if "${@:2}"; then printf 'ok    %s\n' "$1"; else printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); fi
}

a="${work}/artifacts"
v=0.0.31
for pair in x64:x86_64:amd64 arm64:aarch64:arm64; do
  arch="${pair%%:*}" rest="${pair#*:}"; m="${rest%%:*}" deb="${rest##*:}"
  l="${a}/packages-linux-${arch}-source-and-binary"
  mkdir -p "${l}/dist/packages" "${a}/test-reports-${arch}"
  printf 'img' > "${l}/dist/packages/orchestrant-${v}-${m}.AppImage"
  printf 'tar' > "${l}/dist/packages/orchestrant-${v}-linux-${m}.tar.gz"
  printf 'deb' > "${l}/dist/packages/orchestrant_${v}_${deb}.deb"
  printf 'whl' > "${l}/dist/orchestrant-${v}-cp315-cp315-manylinux_${m}.whl"
  printf 'xml' > "${a}/test-reports-${arch}/report.xml"
done
w="${a}/python-package-windows-source-and-binaries"
mkdir -p "${w}/dist/packages" "${w}/logs"
for ext in msi msix zip; do printf 'x64' > "${w}/dist/packages/orchestrant-${v}-windows-x64.${ext}"; done
printf 'cer' > "${w}/dist/packages/orchestrant-${v}-windows-x64-test-signing.cer"
printf 'whl' > "${w}/dist/orchestrant-${v}-cp315-cp315-win_amd64.whl"
printf 'log' > "${w}/logs/build.log"
x="${a}/orchestrant-windows-arm64"
mkdir -p "${x}/packages" "${x}/bundle/runtime"
for ext in msi msix zip; do printf 'arm64' > "${x}/packages/orchestrant-${v}-windows-arm64.${ext}"; done
printf 'cer' > "${x}/packages/orchestrant-${v}-windows-arm64-test-signing.cer"
printf 'exe' > "${x}/bundle/runtime/python.exe"
mkdir -p "${a}/orchestrant-windows-arm64-tests/packages" "${a}/packages-linux-riscv64-source-and-binary/dist"
printf 'msi' > "${a}/orchestrant-windows-arm64-tests/packages/tests.msi"
printf 'whl' > "${a}/packages-linux-riscv64-source-and-binary/dist/orchestrant-${v}-cp315-cp315-linux_riscv64.whl"

out="${work}/release"
check "it collects" bash "${collect}" "${a}" "${out}"
check "the 14 packages of the last tag release, and nothing else" test "$(find "${out}" -type f | wc -l)" = 14
check "both arches' Linux packages" test -e "${out}/orchestrant_${v}_amd64.deb" -a -e "${out}/orchestrant-${v}-linux-aarch64.tar.gz"
check "both Windows arches, with their test certificates" test -e "${out}/orchestrant-${v}-windows-arm64.msix" -a -e "${out}/orchestrant-${v}-windows-x64-test-signing.cer"
check "no wheel, bundle file, report or test package" bash -c "! ls '${out}' | grep -qE '\\.whl$|python\\.exe|report|tests\\.msi'"
check "relative paths, as release.yml passes them" bash -c "cd '${work}' && bash '${collect}' artifacts release-relative >/dev/null && test -e release-relative/orchestrant-${v}-x86_64.AppImage"

mkdir -p "${a}/orchestrant-windows-arm64-copy/packages"
cp "${x}/packages/orchestrant-${v}-windows-arm64.msi" "${a}/orchestrant-windows-arm64-copy/packages/"
check "two artifacts with one file name fail rather than overwrite" bash -c "! bash '${collect}' '${a}' '${work}/release2' >/dev/null 2>&1"
mkdir -p "${work}/none/x"
check "no packages at all fail" bash -c "! bash '${collect}' '${work}/none' '${work}/release3' >/dev/null 2>&1"

if [ "${fails}" -gt 0 ]; then
  printf '%d of the collect checks failed\n' "${fails}"
  exit 1
fi
printf 'collect-release-assets: all checks passed\n'
