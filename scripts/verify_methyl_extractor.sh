#!/bin/bash
# Post-deploy smoke test for GoliathExtractor on worker nodes.
#
# GoliathExtractor is built from a GoliathExtractor checkout via:
#   make && make install
#
# Production workers invoke goliath-extractor on PATH; task config comes from
# resolvedConfig / actionConfig JSON (not worker.env alone).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=methyl_extractor_local.sh
source "$SCRIPT_DIR/methyl_extractor_local.sh"

pass() { echo "  OK: $*"; }
fail() { echo "  FAIL: $*" >&2; exit 1; }

echo "GoliathExtractor verification"
echo "==========================="

BIN="${GOLIATH_EXTRACTOR_BIN:-${METHYL_EXTRACTOR_BIN:-}}"
if [[ -z "$BIN" ]]; then
  if command -v goliath-extractor >/dev/null 2>&1; then
    BIN="$(command -v goliath-extractor)"
  elif command -v MethylExtractor >/dev/null 2>&1; then
    BIN="$(command -v MethylExtractor)"
  else
    BIN="goliath-extractor"
  fi
fi
if command -v "$BIN" >/dev/null 2>&1; then
  pass "GoliathExtractor found: $(command -v "$BIN")"
elif [[ -f "$BIN" && -x "$BIN" ]]; then
  pass "GoliathExtractor found: $BIN"
else
  fail "GoliathExtractor not found (set GOLIATH_EXTRACTOR_BIN or run make install)"
fi

if env -u LD_LIBRARY_PATH -u LD_PRELOAD "$BIN" --help >/dev/null 2>&1; then
  pass "GoliathExtractor --help succeeded"
else
  fail "GoliathExtractor --help failed"
fi

pass "GoliathExtractor environment looks ready"
echo "All checks passed."
