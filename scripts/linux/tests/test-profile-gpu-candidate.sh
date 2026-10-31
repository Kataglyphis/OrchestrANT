#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
selector="$repo/scripts/linux/profile-gpu-candidate.sh"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/packages"
mkdir -p "$fixture/staged/bin"
cat > "$fixture/staged/bin/orchestrant-smoke" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 3 && "$1" == --ort-profile && "$3" == --verify-default ]]
printf '%s\n' "$2"
LAUNCHER
chmod +x "$fixture/staged/bin/orchestrant-smoke"
for name in orchestrant-gpu-nvidia-1.0-linux-x86_64 orchestrant-gpu-nvidia-1.0-linux-aarch64 orchestrant-gpu-amd-1.0-linux-x86_64; do
  tar -C "$fixture" -czf "$fixture/packages/$name.tar.gz" staged
done
[[ "$(bash "$selector" "$fixture" nvidia x64 --print-archive)" == "$fixture/packages/orchestrant-gpu-nvidia-1.0-linux-x86_64.tar.gz" ]]
[[ "$(bash "$selector" "$fixture" nvidia arm64 --print-archive)" == "$fixture/packages/orchestrant-gpu-nvidia-1.0-linux-aarch64.tar.gz" ]]
[[ "$(bash "$selector" "$fixture" amd x64 --print-archive)" == "$fixture/packages/orchestrant-gpu-amd-1.0-linux-x86_64.tar.gz" ]]
[[ "$(RUNNER_TEMP="$fixture" bash "$selector" "$fixture" nvidia x64)" == cuda ]]
[[ "$(RUNNER_TEMP="$fixture" bash "$selector" "$fixture" nvidia arm64)" == cuda ]]
[[ "$(RUNNER_TEMP="$fixture" bash "$selector" "$fixture" amd x64)" == migraphx ]]
if bash "$selector" "$fixture" amd arm64 --print-archive >/dev/null 2>&1; then exit 1; fi
touch "$fixture/packages/orchestrant-gpu-nvidia-2.0-linux-x86_64.tar.gz"
if bash "$selector" "$fixture" nvidia x64 --print-archive >/dev/null 2>&1; then exit 1; fi
rm "$fixture/packages/orchestrant-gpu-nvidia-1.0-linux-aarch64.tar.gz"
if bash "$selector" "$fixture" nvidia arm64 --print-archive >/dev/null 2>&1; then exit 1; fi
echo 'GPU candidate selection fixtures passed'
