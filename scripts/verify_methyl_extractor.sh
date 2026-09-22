#!/bin/bash
# Post-deploy smoke test for MethylExtractor on worker nodes.
#
# MethylExtractor is built from /home/ubuntu/MethylExtractor (or equivalent) via:
#   make deps && make && make install
#
# Production workers invoke MethylExtractor on PATH; task config comes from
# input_json + project step_config.methyl_extract on shared storage.

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

echo "============================================="
echo "MethylExtractor verification"
echo "============================================="
echo ""

BIN="${METHYL_EXTRACTOR_BIN:-MethylExtractor}"
if command -v "$BIN" >/dev/null 2>&1; then
  pass "MethylExtractor found: $(command -v "$BIN")"
elif [[ -x "$BIN" ]]; then
  pass "MethylExtractor found: $BIN"
else
  fail "MethylExtractor not found (set METHYL_EXTRACTOR_BIN or run make install)"
fi

echo ""
echo "Checking --help..."
if "$BIN" --help >/dev/null 2>&1; then
  pass "MethylExtractor --help succeeded"
else
  fail "MethylExtractor --help failed"
fi

echo ""
pass "MethylExtractor environment looks ready"
