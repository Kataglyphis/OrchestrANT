#!/usr/bin/env bash
# Run in WSL: keys | up | status | reload | down. See benchmarks/README.md § Through the gateway.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# The hub cannot know where the pinned tools prompt lives; its raw bytes must match the registry's sha256.
export ANTFRASTRUCTURE_LLM_PROMPTS_DIR="${ANTFRASTRUCTURE_LLM_PROMPTS_DIR:-${KATAGLYPHIS_REPO_ROOT}/benchmarks/prompts}"
# A lab on another registry (LLM_BACKENDS) gets a gateway rendered from that same file.
if [ -n "${LLM_BACKENDS:-}" ]; then
    export ANTFRASTRUCTURE_LLM_BACKENDS="${ANTFRASTRUCTURE_LLM_BACKENDS:-${LLM_BACKENDS}}"
fi

antfrastructure_exec "linux/llm-stack/scripts/serve-stack.sh" "$@"
