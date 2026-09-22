#!/usr/bin/env bash
# Real-data SamplePrep canary: linear + stock Giraffe + methylGrapher WGBS.
#
# Distinct from scripts/smoke_sample_prep.sh (WORKER_STUB_EXTERNAL=1 graph smoke).
# This path requires GPU workers, provisioned FASTQs, and WORKER_STUB_EXTERNAL unset/0.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/smoke_sample_prep_real.sh [options]

Options:
  --tier subset|full     Data tier (default: subset)
  --modes MODE [MODE...] Alignment modes (default: linear pangenome pangenome_wgbs)
  --config PATH          SamplePrepCanaryConfig JSON
  --run-root PATH        Working root (default: .smoke/sample_prep_canary)
  --report-dir PATH      Qualification report directory
  --timeout SEC          Per-mode timeout (default: 14400)
  --dry-run              Preflight + plan only
  --allow-missing-checksums
  --skip-preflight
  -h, --help

Requires:
  - Direct DB env (BACKEND_DB + AZURE_SQL_* or POSTGRES_*)
  - Registered GPU worker with WORKER_STUB_EXTERNAL unset/0
  - Provisioned canary FASTQs (scripts/provision_sample_prep_canary.sh)
  - Site testing.sample_prep_canary or METHYL_SAMPLE_PREP_CANARY_CONFIG
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
fi

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [[ "${WORKER_STUB_EXTERNAL:-0}" == "1" ]]; then
  echo "ERROR: WORKER_STUB_EXTERNAL=1 is set; refuse real canary." >&2
  exit 2
fi

export PYTHONPATH="${REPO_ROOT}/workflow_engine:${REPO_ROOT}/packages/methylutils:${PYTHONPATH:-}"
cd "$REPO_ROOT/workflow_engine"
exec "$PYTHON_BIN" -m ops.sample_prep_canary "${ARGS[@]}"
