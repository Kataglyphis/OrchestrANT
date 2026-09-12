#!/usr/bin/env bash
set -euo pipefail

# Serve the built benchmark viewer on http://localhost:4173.
# Run benchmark-viewer/build-viewer.sh first to build the app.

cd "$(dirname "$0")/benchmark-viewer"

if [ ! -f dist/index.html ]; then
  echo "Error: dist/index.html not found. Run build-viewer.sh first."
  echo "  cd benchmark-viewer && bash build-viewer.sh"
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Benchmark Viewer: http://localhost:4173"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Use nerdctl/docker to run a quick nginx
CMD="nerdctl"
if ! command -v nerdctl &>/dev/null; then
  CMD="docker"
fi

$CMD run -d \
  --name llm-benchmark-viewer \
  -p 4173:80 \
  -v "$(pwd)/dist:/usr/share/nginx/html:ro" \
  nginx:alpine

echo ""
echo "  Running at http://localhost:4173"
echo "  Stop with: $CMD stop llm-benchmark-viewer"
