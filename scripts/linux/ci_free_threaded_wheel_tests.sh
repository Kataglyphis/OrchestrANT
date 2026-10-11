#!/usr/bin/env bash
# Runs tests/unit/test_thread_safety.py against the installed cp314t wheel on 3.14t; ci_packaging.sh calls it after the hub driver.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"
antfrastructure_source "linux/scripts/02-toolchain/python/ci-common.sh"

export WORKSPACE_ROOT="${WORKSPACE_ROOT:-$KATAGLYPHIS_REPO_ROOT}"
prepare_ci_workspace --cd

PYTHON_VERSION="${1:-${PYTHON_VERSION:-3.14}}"
FT_VERSION="$(printf '%s' "${PYTHON_VERSION%t}" | cut -d. -f1,2)t"
FT_ABI="cp$(printf '%s' "${FT_VERSION%t}" | tr -d .)t"
TEST_FILE="tests/unit/test_thread_safety.py"

# The hub driver skips the free-threaded wheel in these two cases, so there is nothing to test.
if [ -n "${PACKAGING_CROSS_TARGET:-}" ]; then
  info "free-threaded wheel tests skipped: the ${PACKAGING_CROSS_TARGET} cross build ships no ${FT_ABI} wheel"
  exit 0
fi
if [ "${PYTHON_FREE_THREADED_WHEEL:-auto}" = off ]; then
  info "free-threaded wheel tests skipped: PYTHON_FREE_THREADED_WHEEL=off"
  exit 0
fi

shopt -s nullglob
wheels=(dist/*-"${FT_ABI}"-*"$(uname -m)".whl)
built=(dist/*)
[ "${#wheels[@]}" -eq 1 ] || err "expected one ${FT_ABI} $(uname -m) wheel in dist/, found ${#wheels[@]} among: ${built[*]##*/}"

ft_python="$(uv python find "${FT_VERSION}" 2>/dev/null)" || err "no ${FT_VERSION} interpreter for the wheel tests"
venv="$WORKSPACE_ROOT/.venv_free_threaded_wheel_tests"
run_dir="$(mktemp -d)"
trap 'rm -rf "${venv}" "${run_dir}"' EXIT

uv venv --python "${ft_python}" --clear "${venv}" || err "cannot create ${venv}"
# The lock's core set plus the test extra, as the 3.14t test leg syncs; the project itself comes from the wheel.
env -u UV_PYTHON -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT="${venv}" \
  uv sync --locked --extra test --no-install-project --python "${venv}/bin/python" \
  || err "the core set and the test extra do not sync on ${FT_VERSION}"
uv pip install --python "${venv}/bin/python" --no-deps "${wheels[0]}" || err "${wheels[0]} does not install into ${venv}"

# Outside the checkout and with -I: the source tree must not shadow the installed wheel.
cp "${TEST_FILE}" "${run_dir}/"
info "free-threaded wheel tests: ${wheels[0]##*/} on $("${venv}/bin/python" -VV)"
(cd "${run_dir}" && ORCHESTRANT_REQUIRE_COMPILED=1 "${venv}/bin/python" -I -m pytest -p no:cacheprovider \
  -W error -W ignore::DeprecationWarning -v "${TEST_FILE##*/}") || err "the thread-safety tests failed against ${wheels[0]##*/}"
info "free-threaded wheel tests passed: ${wheels[0]##*/}"
