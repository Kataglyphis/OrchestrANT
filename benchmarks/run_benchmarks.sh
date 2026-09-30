#!/usr/bin/env bash
set -euo pipefail

# Sweep num_ctx x max_tokens; num_ctx is Ollama-native, so only an Ollama backend makes this meaningful.

cd "$(dirname "$0")"

# An uninstalled checkout needs the repo root on PYTHONPATH for the package's runner.
REPO_ROOT="$(cd .. && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# Same default as the compose service's OLLAMA_PULL_MODELS; BENCH_MODEL overrides both.
MODEL="${BENCH_MODEL:-gemma4:26b}"

BACKEND="${BENCH_BACKEND:-ollama}"
BACKEND_ARGS=(--backend "$BACKEND")

# Refuse rather than emit five identical rows: only Ollama honours num_ctx.
if [[ "$BACKEND" != ollama* && "${BENCH_ALLOW_NON_OLLAMA:-0}" != "1" ]]; then
  echo "  This sweep varies num_ctx, which is Ollama-native — against '$BACKEND'"
  echo "  every config would produce the same run. Use bench_coding.py /"
  echo "  bench_tools.py for other backends, or set BENCH_ALLOW_NON_OLLAMA=1"
  echo "  if you know the endpoint honours num_ctx."
  exit 2
fi
API_URL="$(python3 -c "
from orchestrant.benchmark.openai_api import resolve_backend
print(resolve_backend('$BACKEND')[0])
")/v1"
# Run-scoped: the manifest and the table glob every *.json in OUTDIR.
OUTDIR="${BENCH_OUTDIR:-./benchmark_results/${BACKEND}-$(printf '%s' "$MODEL" | tr '/:' '__')}"
mkdir -p "$OUTDIR"
echo "  Results directory: $OUTDIR"

# num_ctx:max_tokens
CONFIGS=(
  "8192:256"      # baseline
  "16000:256"     # user asked about this
  "8192:4096"     # baseline + long gen
  "16000:4096"    # long ctx + long gen
  "32768:4096"    # pushing further
)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LLM Config Benchmark Suite"
echo "  Backend: $BACKEND"
echo "  Model:   $MODEL"
echo "  API:     $API_URL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Health gate: a broken model is fast, so check its answers before measuring its speed.
if [[ "${BENCH_SKIP_CORRECTNESS:-0}" != "1" ]]; then
  echo "▸ Health gate: verifiable-answer probe"
  set +e
  python3 -m orchestrant.benchmark speed "${BACKEND_ARGS[@]}" --model "$MODEL" --correctness-only
  gate_rc=$?
  set -e
  case "$gate_rc" in
    0) echo "  → model answers correctly; proceeding" ;;
    2) echo ""
       echo "  NOTE: the probe ran out of tokens before the model finished"
       echo "  answering. That is a measurement limit, not a model fault —"
       echo "  raise --correctness-max-tokens if you want a clean verdict." ;;
    *) echo ""
       echo "  WARNING: the model got at least one verifiable answer WRONG."
       echo "  Speed numbers from a broken model are meaningless — inspect the"
       echo "  GGUF first (python3 inspect_gguf.py <file>); sub-4-bit i-quants"
       echo "  are broken on some runtimes. Continuing anyway." ;;
  esac
  echo ""
  echo "──────────────────────────────────────────────────────"
  echo ""
fi

for cfg in "${CONFIGS[@]}"; do
  NUM_CTX="${cfg%%:*}"
  MAX_TOKENS="${cfg##*:}"
  OUTFILE="$OUTDIR/ctx${NUM_CTX}_tok${MAX_TOKENS}.json"

  echo "▸ Config: num_ctx=$NUM_CTX  max_tokens=$MAX_TOKENS"
  echo "  Output: $OUTFILE"
  echo ""

  python3 -m orchestrant.benchmark speed \
    "${BACKEND_ARGS[@]}" \
    --model "$MODEL" \
    --max-tokens "$MAX_TOKENS" \
    --temperature 0.0 \
    --stream \
    --prompts 8 \
    --extra-params "{\"num_ctx\":$NUM_CTX}" \
    --output "$OUTFILE"

  python3 -m orchestrant.benchmark report summary "$OUTFILE" 2>&1

  echo ""
  echo "──────────────────────────────────────────────────────"
  echo ""
done

# Manifest for the Reflex viewer
MANIFEST="$OUTDIR/_manifest.json"
python3 -m orchestrant.benchmark report manifest "$OUTDIR" "$MANIFEST" \
  --title "LLM Benchmark — $MODEL" --model "$MODEL" \
  --generated "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 2>&1

echo ""
echo "All benchmarks complete. Results in $OUTDIR/"
echo ""
echo "Viewer: cd frontend && reflex run"
echo "        (or: ORCHESTRANT_BENCHMARK_MANIFEST=$MANIFEST reflex run)"
echo ""
echo "Quick comparison:"
python3 -m orchestrant.benchmark report table "$OUTDIR" 2>&1

# Opt-in regression check: BENCH_COMPARE_TO=<a previous OUTDIR>.
if [ -n "${BENCH_COMPARE_TO:-}" ]; then
  echo ""
  echo "Regression check against $BENCH_COMPARE_TO:"
  if ! python3 bench_compare.py --dir "$BENCH_COMPARE_TO" "$OUTDIR"; then
    # Advisory by default: a sweep is not a gate unless the operator says so.
    [ "${BENCH_COMPARE_STRICT:-0}" = "1" ] && exit 1
    echo "  (advisory; set BENCH_COMPARE_STRICT=1 to make this fail the run)"
  fi
fi
