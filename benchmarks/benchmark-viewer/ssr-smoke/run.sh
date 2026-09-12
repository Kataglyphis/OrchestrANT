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

node_modules/.bin/esbuild ssr-smoke/entry.jsx \
  --bundle --platform=node --format=cjs --loader:.json=json \
  --outfile=ssr-smoke/out.cjs --log-level=error
node ssr-smoke/out.cjs
