#!/usr/bin/env bash
# The untimed setup of quality-report.sh --perf-host, inside :latest: the app venv and the locked models; it times nothing.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/linux/lib/antfrastructure.sh"
antfrastructure_source linux/scripts/01-core/python_uv.sh

export UV_CACHE_DIR="${REPO_ROOT}/logs/code-quality/perf/uv-cache"
venv="${REPO_ROOT}/.venv-perf-host"
uv_venv_create "${venv}" "${PYTHON_MAJOR_MINOR:?perf-host.sh runs in the family Linux image}"
uv_venv_activate "${venv}"
uv_sync_project

fetcher="$(antfrastructure_path linux/scripts/lib/fetch-data-lock.py)"
python3 -I "${fetcher}" --lock resources/models/llm.lock --dest models --extract
python3 -I "${fetcher}" --lock resources/models/models.lock --dest models
python3 -I "${fetcher}" --lock scripts/linux/ai-regression.lock --dest models/contracts
