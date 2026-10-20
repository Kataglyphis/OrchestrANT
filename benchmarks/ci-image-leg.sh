#!/usr/bin/env bash
# One benchmarks.yml leg in the family image: `bash benchmarks/ci-image-leg.sh pinned|pinnedt <uv run args>`, run in a fresh venv of its X.Y or X.Yt.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

mm="${PYTHON_MAJOR_MINOR:-}"
case "${1:-}" in
  pinned) leg="${mm}" ;;
  pinnedt) leg="${mm}t" ;;
  *) printf 'usage: %s pinned|pinnedt <uv run arguments...>\n' "$0" >&2; exit 2 ;;
esac
shift
# Only the image's interpreters (owner, 2026-10-20): anywhere else uv would download one.
if [ -z "${mm}" ] || ! command -v "python${leg}" >/dev/null 2>&1; then
  printf 'ci-image-leg.sh runs in the family Linux image, which ships python%s\n' "${leg:-X.Y}" >&2
  exit 2
fi

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/linux/lib/antfrastructure.sh"
antfrastructure_source linux/scripts/01-core/python_uv.sh

# A fresh venv, never /opt/venv: its packages (loguru among them) would leak into a --no-project run.
venv="${REPO_ROOT}/.venv-bench-${leg}"
uv_venv_create "${venv}" "${leg}"
export UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT="${venv}"
exec uv run --python "${venv}/bin/python" "$@"
