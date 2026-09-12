#!/usr/bin/env bash
set -euo pipefail

# Run benchmarks across multiple num_ctx and max_tokens configs.
#
# --extra-params are merged into the request body TOP-LEVEL (not via extra_body:
# benchmark_openai_api.py does payload.update(extra_params)).
#
# IMPORTANT: `num_ctx` is Ollama-native. This sweep only means anything against
# an Ollama backend — every other server ignores it, so all five "configs"
# below collapse into the same run and the comparison table shows five rows of
# noise. BENCH_BACKEND lets you point this at a non-Ollama lane; the guard
# below refuses that rather than producing a meaningless table.

cd "$(dirname "$0")"

# The runner moved into the package; its module calls need the repo root on
# PYTHONPATH when OrchestrANT is not installed.
REPO_ROOT="$(cd .. && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# Same default as the compose ollama service's OLLAMA_PULL_MODELS — override
# BOTH with one env var: BENCH_MODEL=<model> (compose pulls it, this benches it).
MODEL="${BENCH_MODEL:-gemma4:26b}"

# Which serving backend to sweep. Names come from backends.json; 'ollama' (this
# stack's own compose service) is the default. BENCH_BACKEND=geniex-npu points
# the same sweep at the Snapdragon lane without editing anything.
BACKEND="${BENCH_BACKEND:-ollama}"
BACKEND_ARGS=(--backend "$BACKEND")

# The configs below vary num_ctx, which only Ollama honours. Refuse rather than
# emit five identical rows dressed up as a comparison.
if [[ "$BACKEND" != ollama* && "${BENCH_ALLOW_NON_OLLAMA:-0}" != "1" ]]; then
  echo "  This sweep varies num_ctx, which is Ollama-native — against '$BACKEND'"
  echo "  every config would produce the same run. Use bench_coding.py /"
  echo "  bench_tools.py for other backends, or set BENCH_ALLOW_NON_OLLAMA=1"
  echo "  if you know the endpoint honours num_ctx."
  exit 2
fi
API_URL="$(python3 -c "
import sys; sys.path.insert(0, '.')
from benchmark_openai_api import resolve_backend
print(resolve_backend('$BACKEND')[0])
")/v1"
# Run-scoped output. The manifest and the comparison table both glob every
# *.json in OUTDIR, so a shared directory silently mixed results from different
# models and backends into one "comparison".
OUTDIR="${BENCH_OUTDIR:-./benchmark_results/${BACKEND}-$(printf '%s' "$MODEL" | tr '/:' '__')}"
mkdir -p "$OUTDIR"
echo "  Results directory: $OUTDIR"

# Configs to test:  (num_ctx x max_tokens)
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

# ── Health gate (LB1) ─────────────────────────────────────────────────────
# A broken model is FAST: the throughput numbers below would look great while
# the output is nonsense. Check the model answers correctly before spending
# hours measuring how quickly it does so. Set BENCH_SKIP_CORRECTNESS=1 to
# bypass (e.g. benchmarking a model the probe's prompts do not suit).
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

  # brief summary (bench_report.py, so it is testable — it used to be a
  # heredoc that no test could reach)
  python3 -m orchestrant.benchmark report summary "$OUTFILE" 2>&1

  echo ""
  echo "──────────────────────────────────────────────────────"
  echo ""
done

# ── Generate manifest for the React viewer ────────────────────────────────
MANIFEST="$OUTDIR/_manifest.json"
python3 -m orchestrant.benchmark report manifest "$OUTDIR" "$MANIFEST" \
  --title "LLM Benchmark — $MODEL" --model "$MODEL" \
  --generated "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 2>&1

echo ""
echo "All benchmarks complete. Results in $OUTDIR/"
echo ""
# OUTDIR is run-scoped; build-viewer.sh copies every run directory and shows
# the newest manifest. Say so here, where the manifest was just written.
echo "Viewer: bash benchmark-viewer/build-viewer.sh"
echo ""
echo "Quick comparison:"
python3 -m orchestrant.benchmark report table "$OUTDIR" 2>&1

# LB11: arm the comparer. It existed, was tested, and nothing ever called it.
# Opt-in: BENCH_COMPARE_TO=<a previous OUTDIR>. docs/refactoring-backlog.md H
if [ -n "${BENCH_COMPARE_TO:-}" ]; then
  echo ""
  echo "Regression check against $BENCH_COMPARE_TO:"
  if ! python3 bench_compare.py --dir "$BENCH_COMPARE_TO" "$OUTDIR"; then
    # Advisory by default: a sweep is not a gate unless the operator says so.
    [ "${BENCH_COMPARE_STRICT:-0}" = "1" ] && exit 1
    echo "  (advisory; set BENCH_COMPARE_STRICT=1 to make this fail the run)"
  fi
fi
