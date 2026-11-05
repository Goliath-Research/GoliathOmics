#!/bin/bash
# Deprecated wrapper — use scripts/package_goliath_extractor.sh
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[DEPRECATED] scripts/package_methyl_extractor.sh is deprecated; use scripts/package_goliath_extractor.sh (see docs/operators/goliath-extractor-migration.md)" >&2
exec bash "$_DIR/package_goliath_extractor.sh" "$@"
