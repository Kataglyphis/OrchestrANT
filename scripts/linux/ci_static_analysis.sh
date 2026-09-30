#!/usr/bin/env bash
# Thin wrapper over the hub's gating driver. Usage: ci_static_analysis.sh [arch] [python_version] [package_name]
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

# Upstream derives it from the distribution name (OrchestrANT), which is not the module directory.
export PACKAGE_NAME="${PACKAGE_NAME:-orchestrant}"

# STATIC_ANALYSIS_EXTRA_PATHS below is space-separated, so no listed path may contain a space.

# The driver never activates its venv (tools in the `test` extra): point uv at its VENV_DIR, forbid re-syncs.
_static_analysis_workspace="${WORKSPACE_ROOT:-$KATAGLYPHIS_REPO_ROOT}"
if [ -d /workspace ] && [ -f /workspace/pyproject.toml ]; then
  _static_analysis_workspace="/workspace"
fi
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-${_static_analysis_workspace}/.venv_static_analysis}"
export UV_NO_SYNC=1

export STATIC_ANALYSIS_EXTRA_PATHS="${STATIC_ANALYSIS_EXTRA_PATHS:-benchmarks frontend bench examples}"

# Replaces the hub default, plus benchmarks/tests; keep equal to -BanditExcludes in scripts/windows/Build-Windows.ps1.
export BANDIT_EXCLUDES="${BANDIT_EXCLUDES:-tests,.venv,.venv_static_analysis,ExternalLib,third_party,archive,docs/test_results,benchmarks/tests}"

antfrastructure_exec "linux/scripts/02-toolchain/python/ci_static_analysis.sh" "$@"
