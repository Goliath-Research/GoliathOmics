#!/bin/bash
# Deprecated wrapper — use scripts/bundle_goliath_extractor_libs.sh
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[DEPRECATED] scripts/bundle_methyl_extractor_libs.sh is deprecated; use scripts/bundle_goliath_extractor_libs.sh (see docs/operators/goliath-extractor-migration.md)" >&2
exec bash "$_DIR/bundle_goliath_extractor_libs.sh" "$@"
