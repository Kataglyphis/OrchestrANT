#!/usr/bin/env bash
# ci_packaging.sh - project wrapper around ContainerHub's generic Python package
# builder (linux/scripts/02-toolchain/python/ci_packaging.sh).
#
# Same dead `flutter/bin:$PATH` test as ci_build_docs.sh had; dropped.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/containerhub.sh"

containerhub_exec "linux/scripts/02-toolchain/python/ci_packaging.sh" "$@"
