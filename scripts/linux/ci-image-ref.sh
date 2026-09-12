#!/usr/bin/env bash
# ci-image-ref.sh - print the family CI container image reference.
#
# A wrapper around ANTfrastructure's linux/scripts/ci-image-ref.sh, which composes
# ${IMAGE_REGISTRY_PREFIX}:${CI_IMAGE_LINUX_TAG|CI_IMAGE_WINDOWS_TAG} from the
# hub's own linux/scripts/01-core/versions.env - the fleet's single source of
# truth for both CI image refs.
#
# WHY THIS EXISTS HERE, GIVEN THIS REPO NAMES NO IMAGE ANYWHERE
# -------------------------------------------------------------
# Both workflows deliberately omit `container-image` / `image` so the reusable
# lane resolves the family image through its own fallback, and
# verify_ci_image_refs.py holds that single literal against versions.env. That
# is the correct state and this script does NOT change it - there is no image
# literal in this repo to deduplicate.
#
# What it buys is the OTHER half of the point: reproducing a CI step on the dev
# box without reading the tag out of the submodule by hand.
#
#   nerdctl run --rm --platform linux/amd64 -v "$PWD:/workspace" -w /workspace \
#     "$(scripts/linux/ci-image-ref.sh)" bash -lc 'scripts/linux/ci_tests.sh'
#
# stdout carries the reference and NOTHING else (upstream sends every
# diagnostic to stderr), so it is safe inside that command substitution.
#
#   ci-image-ref.sh              # the Linux image (default)
#   ci-image-ref.sh --windows    # the Windows image
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/antfrastructure.sh"

antfrastructure_exec "linux/scripts/ci-image-ref.sh" "$@"
