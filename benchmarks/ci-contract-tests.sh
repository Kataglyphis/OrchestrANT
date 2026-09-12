#!/usr/bin/env bash
# ci-contract-tests.sh -- run the benchmark suites against a live
# OpenAI-compatible endpoint. The exact command CI runs, runnable locally.
#
# `-m "not inference"` is the real narrowing: the inference-marked tests need a
# loaded model producing meaningful output and stay local-only. Everything else
# needs only a serving API with >=1 model present.
#
# OLLAMA_BASE_URL and TEST_TIMEOUT are read by tests/unit/benchmark/test_v1_api.py
# itself and are deliberately NOT re-read here - this script takes the endpoint
# it probes as an argument so the readiness loop and the suite cannot end up
# pointed at two different servers.
#
# Usage:
#   bash benchmarks/ci-contract-tests.sh [base-url] [model-tag]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

BASE_URL="http://localhost:11434"
# Explicit `if`, not `[ ... ] && VAR=x`: under `set -e` a false && chain IS the
# command's exit status and would end the script here instead of taking a default.
if [ $# -ge 1 ] && [ -n "$1" ]; then BASE_URL="$1"; fi
# Smallest practical tag; it only has to EXIST in /v1/models - the
# non-inference tests never require a meaningful generation.
MODEL="qwen2.5:0.5b"
if [ $# -ge 2 ] && [ -n "$2" ]; then MODEL="$2"; fi

echo "== waiting for ${BASE_URL} =="
for _ in $(seq 1 30); do
  curl -fsS "${BASE_URL}/api/tags" >/dev/null 2>&1 && break
  sleep 2
done
# Unconditional and unsilenced: if the service never came up this is where the
# run stops, with curl's error, instead of inside pytest 30 lines later.
curl -fsS "${BASE_URL}/api/tags" >/dev/null

echo "== pulling ${MODEL} =="
curl -fsS "${BASE_URL}/api/pull" -d "{\"name\":\"${MODEL}\"}" | tail -c 200
echo

echo "== pytest benchmarks/tests tests/unit/benchmark -m 'not inference' =="
# The unraisable collector is off on purpose: these suites bind and drop stub
# servers, and Python 3.14's session-end GC report turns that into a red run
# after every test has passed. Per-item ResourceWarning filtering is inherited
# from the two benchmark conftests.
python3 -m pytest benchmarks/tests tests/unit/benchmark -v -m "not inference" \
  -p no:unraisableexception
