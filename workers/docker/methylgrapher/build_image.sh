#!/usr/bin/env bash
# Thin wrapper — prefer scripts/build_methylgrapher_image.sh (CI entrypoint).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
exec "${ROOT}/scripts/build_methylgrapher_image.sh" "$@"
