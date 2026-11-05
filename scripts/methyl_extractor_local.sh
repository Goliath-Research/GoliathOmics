#!/bin/bash
# Deprecated wrapper — use scripts/goliath_extractor_local.sh
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "[DEPRECATED] scripts/methyl_extractor_local.sh is deprecated; use scripts/goliath_extractor_local.sh (see docs/operators/goliath-extractor-migration.md)" >&2
  exec bash "$_DIR/goliath_extractor_local.sh" "$@"
fi
echo "[DEPRECATED] sourcing scripts/methyl_extractor_local.sh is deprecated; source scripts/goliath_extractor_local.sh instead (see docs/operators/goliath-extractor-migration.md)" >&2
# shellcheck source=goliath_extractor_local.sh
source "$_DIR/goliath_extractor_local.sh"
