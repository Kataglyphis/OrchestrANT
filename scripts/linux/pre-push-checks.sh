#!/usr/bin/env bash
# This repo's shell suites, run by the hub's pre-push hook after the lint gates: a pin move that turns one red stops the push.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
for suite in scripts/linux/tests/test-*.sh; do bash "${suite}"; done
