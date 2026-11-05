#!/bin/bash
# Deprecated wrapper — use scripts/download_goliath_extractor_artifacts.sh
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[DEPRECATED] scripts/download_methyl_extractor_artifacts.sh is deprecated; use scripts/download_goliath_extractor_artifacts.sh (see docs/operators/goliath-extractor-migration.md)" >&2
exec bash "$_DIR/download_goliath_extractor_artifacts.sh" "$@"
