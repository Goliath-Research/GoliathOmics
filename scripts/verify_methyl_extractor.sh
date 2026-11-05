#!/bin/bash
# Deprecated wrapper — use scripts/verify_goliath_extractor.sh
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[DEPRECATED] scripts/verify_methyl_extractor.sh is deprecated; use scripts/verify_goliath_extractor.sh (see docs/operators/goliath-extractor-migration.md)" >&2
exec bash "$_DIR/verify_goliath_extractor.sh" "$@"
