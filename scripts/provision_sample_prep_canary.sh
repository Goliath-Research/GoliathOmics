#!/usr/bin/env bash
# Provision the pinned GSE261315 / SRR28293403 SamplePrep canary FASTQs.
#
# These reads come from the methylGrapher / HPRC pangenome project (GSE261315).
# Canonical local + QNAP location (same relative tree as graph assets):
#
#   /work/genomes/pangenome/canary/gse261315/SRR28293403/{full,subset}/
#   s3://goliath/genomes/pangenome/canary/gse261315/SRR28293403/{full,subset}/
#
# Downloads the public SRA run once, materializes a deterministic paired-read
# subset, checksums both tiers, and stages under that pangenome tree. Mirror to
# myQNAPcloud with:
#   scripts/sync_genomes_to_s3.sh --only pangenome/canary
#
# Does NOT re-download or re-subsample at canary runtime — the canary consumes
# the immutable objects produced here.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/provision_sample_prep_canary.sh [options]

Options:
  --work-dir PATH       Scratch directory (default: /tmp/sample_prep_canary_SRR28293403)
  --stage-root PATH     Stage root (default: /work/genomes/pangenome)
  --subset-pairs N      Paired reads in the subset tier (default: 2000000)
  --skip-download       Reuse existing FASTQs under --work-dir
  --dry-run             Print planned actions only
  -h, --help            Show help

Canonical layout (pangenome project provenance):
  /work/genomes/pangenome/canary/gse261315/SRR28293403/full/
  /work/genomes/pangenome/canary/gse261315/SRR28293403/subset/
  s3://goliath/genomes/pangenome/canary/gse261315/SRR28293403/...

Requires (for a full provision):
  - prefetch / fasterq-dump (SRA Toolkit) OR existing *_1.fastq.gz / *_2.fastq.gz
  - gzip, sha256sum, python3
  - enough disk for ~16GB SRA + expanded FASTQ + subset

After staging:
  1) scripts/sync_genomes_to_s3.sh --only pangenome/canary
  2) Merge checksums into site testing.sample_prep_canary
     (fastq_storage.basePath=/work/genomes/pangenome)
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_ACC="SRR28293403"
GEO_SAMPLE="GSM8140413"
WORK_DIR="/tmp/sample_prep_canary_${RUN_ACC}"
# Default under the pangenome inventory tree (mirrored to QNAP genomes/pangenome/).
STAGE_ROOT="/work/genomes/pangenome"
SUBSET_PAIRS=2000000
SKIP_DOWNLOAD=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --work-dir) WORK_DIR="${2:-}"; shift 2 ;;
    --stage-root) STAGE_ROOT="${2:-}"; shift 2 ;;
    --subset-pairs) SUBSET_PAIRS="${2:-}"; shift 2 ;;
    --skip-download) SKIP_DOWNLOAD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

FULL_PREFIX="canary/gse261315/${RUN_ACC}/full"
SUBSET_PREFIX="canary/gse261315/${RUN_ACC}/subset"
R1_NAME="${RUN_ACC}_1.fastq.gz"
R2_NAME="${RUN_ACC}_2.fastq.gz"

echo "SamplePrep canary provision (pangenome inventory tree)"
echo "  run=${RUN_ACC} geo=${GEO_SAMPLE} subset_pairs=${SUBSET_PAIRS}"
echo "  work_dir=${WORK_DIR}"
echo "  stage_root=${STAGE_ROOT}"
echo "  local: ${STAGE_ROOT}/${FULL_PREFIX}/"
echo "  qnap:  s3://goliath/genomes/pangenome/${FULL_PREFIX}/"

if [[ "$DRY_RUN" -eq 1 ]]; then
  cat <<EOF
dry-run plan:
  1) prefetch/fasterq-dump ${RUN_ACC} -> ${WORK_DIR}/full/${R1_NAME} + ${R2_NAME}
  2) write first ${SUBSET_PAIRS} pairs -> ${WORK_DIR}/subset/
  3) sha256sum both tiers
  4) stage under ${STAGE_ROOT}/${FULL_PREFIX}/ and .../${SUBSET_PREFIX}/
  5) write ${STAGE_ROOT}/canary/gse261315/${RUN_ACC}/checksums.json
  6) mirror: scripts/sync_genomes_to_s3.sh --only pangenome/canary
EOF
  exit 0
fi

mkdir -p "$WORK_DIR/full" "$WORK_DIR/subset" \
  "$STAGE_ROOT/$FULL_PREFIX" "$STAGE_ROOT/$SUBSET_PREFIX" \
  "$STAGE_ROOT/canary/gse261315/${RUN_ACC}"

FULL_R1="$WORK_DIR/full/$R1_NAME"
FULL_R2="$WORK_DIR/full/$R2_NAME"

if [[ "$SKIP_DOWNLOAD" -eq 0 ]]; then
  if [[ ! -f "$FULL_R1" || ! -f "$FULL_R2" ]]; then
    if ! command -v fasterq-dump >/dev/null 2>&1 && ! command -v prefetch >/dev/null 2>&1; then
      echo "ERROR: SRA Toolkit (prefetch/fasterq-dump) not found and FASTQs missing." >&2
      echo "Install on Ubuntu/Debian:" >&2
      echo "  sudo apt-get update && sudo apt-get install -y sra-toolkit" >&2
      echo "Or place ${R1_NAME}/${R2_NAME} under ${WORK_DIR}/full/ and re-run with --skip-download." >&2
      exit 2
    fi
    echo "Downloading ${RUN_ACC} (large; may take hours)..."
    cd "$WORK_DIR"
    if command -v prefetch >/dev/null 2>&1; then
      prefetch "$RUN_ACC"
    fi
    fasterq-dump --split-files --threads "${FASTERQ_THREADS:-4}" -O "$WORK_DIR/full" "$RUN_ACC"
    # Compress if tool emitted uncompressed FASTQ
    if [[ -f "$WORK_DIR/full/${RUN_ACC}_1.fastq" && ! -f "$FULL_R1" ]]; then
      gzip -c "$WORK_DIR/full/${RUN_ACC}_1.fastq" >"$FULL_R1"
      gzip -c "$WORK_DIR/full/${RUN_ACC}_2.fastq" >"$FULL_R2"
    fi
  fi
fi

if [[ ! -f "$FULL_R1" || ! -f "$FULL_R2" ]]; then
  echo "ERROR: missing full FASTQs: $FULL_R1 / $FULL_R2" >&2
  exit 2
fi

SUBSET_R1="$WORK_DIR/subset/$R1_NAME"
SUBSET_R2="$WORK_DIR/subset/$R2_NAME"
# Prefer uncompressed FASTQs for subsetting (much faster than gunzip-streaming).
SUBSET_SRC_R1="$FULL_R1"
SUBSET_SRC_R2="$FULL_R2"
if [[ -f "$WORK_DIR/full/${RUN_ACC}_1.fastq" && -f "$WORK_DIR/full/${RUN_ACC}_2.fastq" ]]; then
  SUBSET_SRC_R1="$WORK_DIR/full/${RUN_ACC}_1.fastq"
  SUBSET_SRC_R2="$WORK_DIR/full/${RUN_ACC}_2.fastq"
fi
if [[ ! -f "$SUBSET_R1" || ! -f "$SUBSET_R2" ]]; then
  echo "Writing deterministic first-${SUBSET_PAIRS}-pair subset from $(basename "$SUBSET_SRC_R1")..."
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
  fi
  "$PYTHON_BIN" - "$SUBSET_SRC_R1" "$SUBSET_SRC_R2" "$SUBSET_R1" "$SUBSET_R2" "$SUBSET_PAIRS" <<'PY'
from pathlib import Path
import gzip
import sys

r1_in = Path(sys.argv[1])
r2_in = Path(sys.argv[2])
r1_out = Path(sys.argv[3])
r2_out = Path(sys.argv[4])
n_pairs = int(sys.argv[5])

def open_maybe(path: Path, mode: str):
    if str(path).endswith(".gz"):
        return gzip.open(path, mode)
    return open(path, mode)

written = 0
with open_maybe(r1_in, "rt") as i1, open_maybe(r2_in, "rt") as i2, \
     open_maybe(r1_out, "wt") as o1, open_maybe(r2_out, "wt") as o2:
    while written < n_pairs:
        b1 = [i1.readline() for _ in range(4)]
        b2 = [i2.readline() for _ in range(4)]
        if not b1[0] or not b2[0]:
            break
        o1.writelines(b1)
        o2.writelines(b2)
        written += 1
print(f"subset_pairs_written={written}")
if written <= 0:
    raise SystemExit("subset write produced zero pairs")
PY
fi

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

FULL_R1_SHA="$(sha256_file "$FULL_R1")"
FULL_R2_SHA="$(sha256_file "$FULL_R2")"
SUB_R1_SHA="$(sha256_file "$SUBSET_R1")"
SUB_R2_SHA="$(sha256_file "$SUBSET_R2")"

cp -f "$FULL_R1" "$STAGE_ROOT/$FULL_PREFIX/$R1_NAME"
cp -f "$FULL_R2" "$STAGE_ROOT/$FULL_PREFIX/$R2_NAME"
cp -f "$SUBSET_R1" "$STAGE_ROOT/$SUBSET_PREFIX/$R1_NAME"
cp -f "$SUBSET_R2" "$STAGE_ROOT/$SUBSET_PREFIX/$R2_NAME"

CHECKSUMS="$STAGE_ROOT/canary/gse261315/${RUN_ACC}/checksums.json"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
fi
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path
payload = {
  "sra_run": "${RUN_ACC}",
  "geo_sample": "${GEO_SAMPLE}",
  "subset_read_pairs": int(${SUBSET_PAIRS}),
  "full": {
    "prefix": "${FULL_PREFIX}/",
    "r1_name": "${R1_NAME}",
    "r2_name": "${R2_NAME}",
    "r1_sha256": "${FULL_R1_SHA}",
    "r2_sha256": "${FULL_R2_SHA}",
    "size_bytes_r1": Path("${FULL_R1}").stat().st_size,
    "size_bytes_r2": Path("${FULL_R2}").stat().st_size,
  },
  "subset": {
    "prefix": "${SUBSET_PREFIX}/",
    "r1_name": "${R1_NAME}",
    "r2_name": "${R2_NAME}",
    "r1_sha256": "${SUB_R1_SHA}",
    "r2_sha256": "${SUB_R2_SHA}",
    "expected_read_pairs": int(${SUBSET_PAIRS}),
    "size_bytes_r1": Path("${SUBSET_R1}").stat().st_size,
    "size_bytes_r2": Path("${SUBSET_R2}").stat().st_size,
  },
}
Path("${CHECKSUMS}").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY

cp -f "$REPO_ROOT/tests/real_data/sample_prep_canary/provenance.json" \
  "$STAGE_ROOT/canary/gse261315/${RUN_ACC}/provenance.json"

echo "Staged under ${STAGE_ROOT}"
echo "Next:"
echo "  1) scripts/sync_genomes_to_s3.sh --only pangenome/canary"
echo "  2) Merge checksums into site testing.sample_prep_canary"
echo "     (fastq_storage.basePath=${STAGE_ROOT})"
