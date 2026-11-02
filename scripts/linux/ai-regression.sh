#!/usr/bin/env bash
# ai-regression.yml inside :latest: contract probes on the tiny model and SmolLM2-135M, the text-generation-v1 replay and the two-cats detector; wall time is recorded, never graded.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/linux/lib/antfrastructure.sh"
antfrastructure_source linux/scripts/01-core/python_uv.sh

out="${AI_REGRESSION_OUT:-logs/ai-regression}"
models="${AI_REGRESSION_MODELS:-models}"
rm -rf "${out}"
mkdir -p "${out}" "${models}"

venv="${REPO_ROOT}/.venv-ai-regression"
uv_venv_create "${venv}" "${PYTHON_MAJOR_MINOR:?ai-regression.sh runs in the family Linux image}"
uv_venv_activate "${venv}"
uv_sync_project
py="${venv}/bin/python"

fetcher="$(antfrastructure_path linux/scripts/lib/fetch-data-lock.py)"
python3 -I "${fetcher}" --lock resources/models/llm.lock --dest "${models}" --extract
python3 -I "${fetcher}" --lock resources/models/models.lock --dest "${models}"
python3 -I "${fetcher}" --lock scripts/linux/ai-regression.lock --dest "${models}/contracts"
rm -rf "${models}/tiny-genai"
"${py}" "$(antfrastructure_path linux/scripts/lib/tiny-genai-model.py)" --out "${models}/tiny-genai"

servers=()
# Every background job here is a server, so a bare wait reaps them all.
stop_servers() {
  [ "${#servers[@]}" -eq 0 ] || kill -TERM "${servers[@]}" 2>/dev/null || :
  wait || :
  servers=()
}
trap stop_servers EXIT

# probe <name> <model dir> <port> <timed: 0|1> <contract args...>: serve one model, probe its contract, optionally time the streamed prompts.
probe() {
  local name="$1" dir="$2" port="$3" timed="$4" base
  shift 4
  base="http://127.0.0.1:${port}"
  "${venv}/bin/orchestrant-serve" --model-dir "${dir}" --port "${port}" --model-id "${name}" \
    > "${out}/serve-${name}.log" 2>&1 &
  servers+=("$!")
  curl -fsS --retry 120 --retry-connrefused --retry-delay 1 "${base}/v1/models" > /dev/null
  "${venv}/bin/orchestrant-bench" contract --base-url "${base}" --model "${name}" \
    --output "${out}/contract-${name}.json" "$@"
  if [ "${timed}" = 1 ]; then
    "${venv}/bin/orchestrant-bench" speed --base-url "${base}" --model "${name}" --stream \
      --max-tokens 32 --no-energy --idle-seconds 0 --output "${out}/speed-${name}.json"
  fi
}

# The tiny model's 512-token context holds only these requests; the others are refused with 400, an error answer by design.
tiny_checks=max_tokens_honoured,usage_reported,prompt_tokens_survive_cache,stream_usage,temperature0_deterministic
tiny_checks+=,greedy_deterministic,temperature0_is_greedy,identical_repeat_intact,seed_deterministic,chat_stop_honoured
tiny_checks+=,power_mode_understood,bundle_system_prompt
# orchestrant-serve has no /v1/completions route, so completions_stop_honoured would probe a 404.
smol_checks=max_tokens_honoured,output_cap,usage_reported,prompt_tokens_survive_cache,stream_usage
smol_checks+=,temperature0_deterministic,greedy_deterministic,temperature0_is_greedy,identical_repeat_intact
smol_checks+=,seed_deterministic,emits_think,chat_stop_honoured,tool_calls_parsed,response_format_json_schema
smol_checks+=,prefix_cache,prefix_cache_extend,prefix_cache_fork,overflow_is_clean_error,power_mode_understood
smol_checks+=,bundle_system_prompt
# The tiny model answers in under the speed report's 10 ms resolution, so only SmolLM2 is timed.
probe tiny-genai "${models}/tiny-genai" 18301 0 --only "${tiny_checks}" --timeout 60
probe smollm2_135m_instruct-cpu "${models}/smollm2_135m_instruct-cpu" 18302 1 --only "${smol_checks}" \
  --prefix-tokens 500 --timeout 300
stop_servers

status=0
python3 -I scripts/linux/ai_regression_verdicts.py "${out}"/contract-*.json --summary "${out}/contract-answers.md" || status=1
"${py}" scripts/linux/ai_regression_run.py replay --contract "${models}/contracts/text-generation-v1.json" \
  --models "${models}" > "${out}/replay.txt" || status=1
cat "${out}/replay.txt"
"${py}" scripts/linux/ai_regression_run.py detector --model "${models}/$("${py}" -c 'from orchestrant.paths import default_model_name as n; print(n())')" \
  --fixture "${models}/contracts/two_cats.jpg" --out "${out}/detector" || status=1
python3 -I "$(antfrastructure_path shared/python/compare_detector_parity.py)" \
  "${out}/detector/detection-1.json" "${out}/detector/detection-2.json" || status=1
"${py}" scripts/linux/ai_regression_run.py latency --detector "${out}/detector/latency-ms.json" \
  --speed "smollm2_135m=${out}/speed-smollm2_135m_instruct-cpu.json" \
  --out "${out}/ai-regression-bench.json" || status=1
exit "${status}"
