#!/usr/bin/env bash
# serve-stack.sh - the llm-stack gateway (APISIX in front of the GenieX lanes),
# rendered from the registry the lab reads. A wrapper around ANTfrastructure's
# linux/llm-stack/scripts/serve-stack.sh, which owns the renderer, the
# validation, the compose overlay and the reload contract. Run it in WSL:
#
#   scripts/linux/serve-stack.sh keys | up | status | reload | down
#
# How it works: third_party/ANTfrastructure/linux/llm-stack/README.md § Gateway;
# benchmarking through it: benchmarks/README.md § Through the gateway.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# The one thing the hub cannot know: where the pinned tools prompt lives. The
# renderer checks its raw bytes against the registry's sha256 (.gitattributes).
export ANTFRASTRUCTURE_LLM_PROMPTS_DIR="${ANTFRASTRUCTURE_LLM_PROMPTS_DIR:-${KATAGLYPHIS_REPO_ROOT}/benchmarks/prompts}"
# A lab pointed at another registry (LLM_BACKENDS) gets a gateway rendered from
# that same file, so the lab-* entries and the lanes behind them agree.
if [ -n "${LLM_BACKENDS:-}" ]; then
    export ANTFRASTRUCTURE_LLM_BACKENDS="${ANTFRASTRUCTURE_LLM_BACKENDS:-${LLM_BACKENDS}}"
fi

antfrastructure_exec "linux/llm-stack/scripts/serve-stack.sh" "$@"
