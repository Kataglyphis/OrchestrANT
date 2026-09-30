#!/usr/bin/env bash
# CI's contract-test command, runnable locally: bash benchmarks/ci-contract-tests.sh [base-url] [model-tag]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

BASE_URL="http://localhost:11434"
# Explicit `if`: under `set -e` a false `[ ... ] && VAR=x` would end the script.
if [ $# -ge 1 ] && [ -n "$1" ]; then BASE_URL="$1"; fi
# Smallest practical tag: the non-inference tests only need it to exist in /v1/models.
MODEL="qwen2.5:0.5b"
if [ $# -ge 2 ] && [ -n "$2" ]; then MODEL="$2"; fi

echo "== waiting for ${BASE_URL} =="
for _ in $(seq 1 30); do
  curl -fsS "${BASE_URL}/api/tags" >/dev/null 2>&1 && break
  sleep 2
done
# Unsilenced on purpose: a service that never came up stops the run here, with curl's error.
curl -fsS "${BASE_URL}/api/tags" >/dev/null

echo "== pulling ${MODEL} =="
curl -fsS "${BASE_URL}/api/pull" -d "{\"name\":\"${MODEL}\"}" | tail -c 200
echo

echo "== pytest benchmarks/tests tests/unit/benchmark -m 'not inference' =="
# Unraisable collector off: Python 3.14's session-end GC report reddens a run over dropped stub servers.
python3 -m pytest benchmarks/tests tests/unit/benchmark -v -m "not inference" \
  -p no:unraisableexception
