#!/bin/bash
# Deprecated wrapper — use scripts/install_goliath_extractor_tarball.sh
set -euo pipefail
_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[DEPRECATED] scripts/install_methyl_extractor_tarball.sh is deprecated; use scripts/install_goliath_extractor_tarball.sh (see docs/operators/goliath-extractor-migration.md)" >&2
exec bash "$_DIR/install_goliath_extractor_tarball.sh" "$@"
