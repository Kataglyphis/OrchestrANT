#!/usr/bin/env bash
# Render the viewer's components against the REAL manifest, without a browser.
#
# `vite build` only proves the JSX compiles. It cannot catch a component that
# throws on first render, nor an edit that silently failed to apply -- both of
# which happened while these metrics were being added. This renders every
# component server-side with actual benchmark data (including older result
# files that predate the streaming metrics) and asserts the new numbers really
# reach the DOM.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d node_modules ]]; then
  echo "node_modules missing — run 'npm install' in benchmark-viewer first" >&2
  exit 1
fi
if [[ ! -f ../benchmark_results/_manifest.json ]]; then
  echo "no ../benchmark_results/_manifest.json — run run_benchmarks.sh first" >&2
  exit 1
fi

# Bundle the SSR entry with the project's own Vite. Vite 8 bundles with
# Rolldown and no longer ships esbuild, so the old
# `node_modules/.bin/esbuild` call found no binary and the smoke could not run
# at all.
node_modules/.bin/vite build --ssr ssr-smoke/entry.jsx \
  --outDir ssr-smoke/out --emptyOutDir --logLevel error
node ssr-smoke/out/entry.js
