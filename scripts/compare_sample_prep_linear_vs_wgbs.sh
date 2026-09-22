#!/usr/bin/env bash
# Linear vs pangenome_wgbs SamplePrep comparison (experiment-only mode trees).
#
# FASTQs stay at /work/samples/<sampleId>/ (lab/QNAP shape). Mode subdirs
# linear/ and pangenome_wgbs/ are temporary scaffolding for dual-align compare.
# sampleStorage is omitted — do not archive to QNAP until after review.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/compare_sample_prep_linear_vs_wgbs.sh [options]

Options:
  --samples-json PATH       Samples list JSON (default: plasma + buffy pair)
  --fastq-storage-json PATH QNAP/lab fastqStorage for one-time root download
  --thresholds-json PATH    Operator-set comparison thresholds
  --samples-base PATH       Default /work/samples
  --report-dir PATH         Default /work/samples/_comparisons
  --reuse-local-fastq       Use existing non-empty root FASTQs (no download)
  --report-only             Validate existing mode trees; write report
  --dry-run                 Plan start payloads only
  --timeout SEC             Per-arm timeout (default 28800)
  --modes LIST              Arms to run (default linear,pangenome_wgbs). The
                            pangenome arm is CPU-only and far slower, so run it
                            on its own timeout and pair results with --report-only
  -h, --help

Requires for real runs:
  - Direct DB env (BACKEND_DB + AZURE_SQL_* or POSTGRES_*)
  - GPU worker with WORKER_STUB_EXTERNAL unset/0
  - Site linear + pangenome_wgbs pins; METHYL_METHYLGRAPHER_IMAGE
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
  # Allow dry-run / report-only through the Python CLI even if stub is set.
  for a in "${ARGS[@]+"${ARGS[@]}"}"; do
    if [[ "$a" == "--dry-run" || "$a" == "--report-only" ]]; then
      stub_ok=1
      break
    fi
  done
  if [[ -z "${stub_ok:-}" ]]; then
    echo "ERROR: WORKER_STUB_EXTERNAL=1 is set; refuse real compare." >&2
    exit 2
  fi
fi

export PYTHONPATH="${REPO_ROOT}/workflow_engine:${REPO_ROOT}/packages/methylutils:${REPO_ROOT}/packages/methyldomain:${REPO_ROOT}/workers:${PYTHONPATH:-}"
cd "$REPO_ROOT/workflow_engine"
exec "$PYTHON_BIN" -m ops.sample_prep_mode_compare "${ARGS[@]}"
