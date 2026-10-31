#!/bin/bash
# End-to-end node verification for GPU worker hosts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=worker_venv_local.sh
source "$SCRIPT_DIR/worker_venv_local.sh"

ENV_FILE="${GOLIATH_ENV_FILE:-/work/goliath/env/worker.env}"
GOLIATH_ROOT="${GOLIATH_ROOT:-/work/goliath}"
ARCH="$(platform_arch_key "$(detect_uname_arch)")"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

LOCAL_VENV="$(worker_venv_local_dir "$ARCH")"
if [[ -f "$LOCAL_VENV/bin/activate" ]]; then
  # shellcheck disable=SC1090,SC1091
  source "$LOCAL_VENV/bin/activate"
elif [[ -f "$SCRIPT_DIR/../.venv/bin/activate" ]]; then
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/../.venv/bin/activate"
fi

echo "============================================="
echo "E2E node verification"
echo "============================================="
echo ""

FAIL=0

run_check() {
  local name="$1"
  shift
  if "$@"; then
    echo "OK  $name"
  else
    echo "FAIL $name"
    FAIL=1
  fi
}

VERIFY_SETUP_ARGS=()
_cur="$(goliath_current_dir "$GOLIATH_ROOT" 2>/dev/null || true)"
if [[ -n "$_cur" && -d "$_cur/runtime-bundle" ]]; then
  VERIFY_SETUP_ARGS=(--runtime-bundle)
  export GOLIATH_ROOT
fi
run_check "verify_setup" bash "$SCRIPT_DIR/verify_setup.sh" "${VERIFY_SETUP_ARGS[@]}"

# Per-VM apt tools (samtools/bedtools/fastp) — not provided by shared /work.
run_check "verify_host_tools" bash "$SCRIPT_DIR/verify_host_tools.sh"

if command -v nvidia-smi >/dev/null 2>&1 && [[ -n "${METHYL_PARABRICKS_IMAGE:-}" ]]; then
  run_check "verify_parabricks" bash "$SCRIPT_DIR/verify_parabricks.sh"
else
  echo "SKIP verify_parabricks (no GPU image or nvidia-smi)"
fi

if [[ -n "${GOLIATH_EXTRACTOR_BIN:-}${METHYL_EXTRACTOR_BIN:-}" ]] \
  || command -v goliath-extractor >/dev/null 2>&1 \
  || command -v MethylExtractor >/dev/null 2>&1; then
  run_check "verify_methyl_extractor" bash "$SCRIPT_DIR/verify_methyl_extractor.sh"
else
  echo "SKIP verify_methyl_extractor (GoliathExtractor not configured)"
fi

run_check "methyl-worker --help" methyl-worker --help

if [[ -n "${WORKER_ID:-}" && -n "${WORKER_TOKEN:-}" && -n "${WORKER_API_BASE:-}" ]]; then
  echo "Worker credentials present in environment (WORKER_ID=$WORKER_ID)"
elif [[ -f /etc/methyl/worker-token ]]; then
  echo "OK  enrolled token file /etc/methyl/worker-token"
else
  echo "WARN worker not enrolled; set WORKER_API_BASE and run methyl-worker enroll (portal IP prereg required)"
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "E2E node verification passed"
  exit 0
fi
echo "E2E node verification failed"
exit 1
