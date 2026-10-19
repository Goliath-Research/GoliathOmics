#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/goliath-align/bin" "$TMP/goliath-align/mojo-env/bin"
printf '#!/bin/sh\necho help-ok\n' >"$TMP/goliath-align/bin/methylGrapher"
chmod +x "$TMP/goliath-align/bin/methylGrapher"
# fake mojo so structure matches
printf '#!/bin/sh\n' >"$TMP/goliath-align/mojo-env/bin/mojo"
chmod +x "$TMP/goliath-align/mojo-env/bin/mojo"
tar -C "$TMP" -czf "$TMP/ga.tgz" goliath-align
# shellcheck source=../goliath_align_local.sh
source "$ROOT/scripts/goliath_align_local.sh"
export GOLIATH_LOCAL_ROOT="$TMP/local"
install_goliath_align_local "$TMP/ga.tgz" amd64
[[ -x "$METHYL_METHYLGRAPHER_BIN" ]] || { echo FAIL; exit 1; }
"$METHYL_METHYLGRAPHER_BIN" | grep -q help-ok
echo "PASS: goliath_align_local"
