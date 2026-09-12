#!/usr/bin/env bash
set -euo pipefail

# Build the React benchmark viewer app using a Node container.
# Output goes to benchmark-viewer/dist/.
# The built files can be served from any HTTP server.

cd "$(dirname "$0")"

# Copy the run-scoped tree, then promote the newest run's manifest to where
# App.jsx looks. Both paths are absolute or relative to THIS directory.
copy_results() {
  # A trailing slash on SRC made the prefix strip a no-op, writing outside $dst
  # for a relative SRC. Normalise, then assert the strip happened.
  local src="${1%/}" dst="${2%/}"
  mkdir -p "$dst"

  local copied=0 rel
  while IFS= read -r -d '' file; do
    rel="${file#"$src"/}"
    if [ "$rel" = "$file" ]; then
      echo "copy_results: $file is not under $src" >&2
      return 1
    fi
    mkdir -p "$dst/$(dirname "$rel")"
    cp "$file" "$dst/$rel"
    copied=$((copied + 1))
  done < <(find "$src" -type f -name '*.json' -print0 2>/dev/null || true)

  if [ "$copied" -eq 0 ]; then
    echo "  (no benchmark_results JSON yet — viewer built without data)"
    return 0
  fi
  echo "  ✓ copied $copied JSON file(s), run subdirectories included"

  # Newest manifest wins, chosen in the SOURCE tree: cp does not preserve
  # mtimes, so a promoted copy would always look like the newest.
  local manifests=() newest
  while IFS= read -r line; do
    [ -n "$line" ] && manifests+=("$line")
  done < <(find "$src" -type f -name '_manifest.json' -printf '%T@ %p\n' 2>/dev/null \
           | sort -rn | cut -d' ' -f2- || true)

  if [ "${#manifests[@]}" -eq 0 ]; then
    echo "  WARNING: no _manifest.json anywhere under $src — the viewer will"
    echo "           report 'benchmark_results/_manifest.json' missing."
    return 0
  fi
  newest="${manifests[0]}"
  if [ "$newest" != "$src/_manifest.json" ]; then
    cp "$newest" "$dst/_manifest.json"
    echo "  ✓ viewer manifest ← ${newest#"$src"/}"
  fi
}

# The copy step alone, so it can be tested without building a container.
if [ "${1:-}" = "--copy-only" ]; then
  copy_results "${2:-../benchmark_results}" "${3:-dist/benchmark_results}"
  exit 0
fi

# Node major from the canonical NODE_VERSION pin (versions.env) — was a
# hardcoded node:20-alpine that silently drifted from the 26.x pin (backlog
# 2026-08-10). Major-only tag: the viewer build needs a Node runtime, not a
# byte-pinned toolchain, and alpine tags exist per-major reliably.
_VERSIONS_ENV="$(cd ../.. && pwd)/third_party/ANTfrastructure/linux/scripts/01-core/versions.env"
NODE_MAJOR="$(grep -E '^NODE_VERSION=' "${_VERSIONS_ENV}" | head -1 | cut -d= -f2 | cut -d. -f1)"
NODE_IMAGE="node:${NODE_MAJOR:-20}-alpine"

# We mount the repo root so Vite can resolve the @import of brand.css
# from the DocumANTation submodule at build time.
REPO_ROOT="$(cd ../.. && pwd)"
SRC_DIR="/repo"
VIEWER_DIR="$SRC_DIR/benchmarks/benchmark-viewer"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Building benchmark viewer (Vite + React)"
echo "  Using image: $NODE_IMAGE"
echo "  Repo root: $REPO_ROOT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Use nerdctl or docker
CMD="nerdctl"
if ! command -v nerdctl &>/dev/null; then
  CMD="docker"
fi

$CMD run --rm \
  -v "$REPO_ROOT:$SRC_DIR" \
  -w "$VIEWER_DIR" \
  "$NODE_IMAGE" \
  sh -euc "
    npm ci
    npm run build
    echo ''
    echo '✓ Build complete. Files in dist/:'
    ls -lh dist/
  "
# -euc above: the old &&-chain ended at the first echo, so with a STALE dist/
# from an earlier run a failed npm build still exited 0 (the container's status
# was ls's) and this script printed the success banner over a broken build.

# Copy benchmark results into dist/ so the viewer can load them
# (avoids needing multiple volume mounts at serve time)
echo ""
echo "  Copying benchmark_results/ into dist/ …"
# Guarded inside copy_results: zero results is a fresh checkout, not a build
# failure — the bare glob aborted under set -e right AFTER a successful build.
copy_results ../benchmark_results dist/benchmark_results
ls -lh dist/benchmark_results/

echo ""
echo "To serve:"
echo "  python3 -m http.server 4173 -d dist/"
echo "  # or open dist/index.html directly in a browser"
