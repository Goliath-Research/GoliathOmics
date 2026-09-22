#!/usr/bin/env bash
# Optional MethylDackel comparison extract (NOT a production SamplePrep action).
#
# Stages upstream MethylDackel cytosine / bedGraph outputs under:
#   /work/samples/<sampleId>/extract.methyldackel/
#
# Production extract remains MethylExtractor (sample.methyl_extract) or
# methylGrapher MethylCall for pangenome_wgbs.
#
# Usage:
#   scripts/compare_extract_methyldackel.sh \
#     --bam /work/samples/S1/align.linear.parabricks/S1.bam \
#     --ref /work/genomes/linear/.../ref.fa \
#     --out-dir /work/samples/S1/extract.methyldackel \
#     [--sample-id S1] [--methyl-dackel /path/to/MethylDackel]
#
# Env:
#   METHYLDACKEL_BIN  — override binary (default: MethylDackel on PATH)
set -euo pipefail

SAMPLE_ID=""
BAM=""
REF=""
OUT_DIR=""
MD_BIN="${METHYLDACKEL_BIN:-MethylDackel}"
THREADS="${METHYLDACKEL_THREADS:-4}"

usage() {
  sed -n '1,25p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bam) BAM="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --sample-id) SAMPLE_ID="$2"; shift 2 ;;
    --methyl-dackel) MD_BIN="$2"; shift 2 ;;
    --threads) THREADS="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown arg: $1" >&2; usage 1 ;;
  esac
done

[[ -n "$BAM" && -n "$REF" && -n "$OUT_DIR" ]] || {
  echo "ERROR: --bam, --ref, and --out-dir are required" >&2
  usage 1
}
[[ -f "$BAM" ]] || { echo "ERROR: BAM not found: $BAM" >&2; exit 1; }
[[ -f "$REF" ]] || { echo "ERROR: reference not found: $REF" >&2; exit 1; }

if ! command -v "$MD_BIN" >/dev/null 2>&1 && [[ ! -x "$MD_BIN" ]]; then
  cat >&2 <<EOF
ERROR: MethylDackel binary not found ($MD_BIN).
Install upstream MethylDackel for optional extract A/B, or set METHYLDACKEL_BIN.
Production pipelines use MethylExtractor — this script is comparison-only.
EOF
  exit 2
fi

mkdir -p "$OUT_DIR"
SAMPLE_ID="${SAMPLE_ID:-$(basename "$(dirname "$OUT_DIR")")}"
PREFIX="$OUT_DIR/${SAMPLE_ID}"

log() { printf '[compare-extract-methyldackel] %s\n' "$*"; }

log "running MethylDackel extract on $BAM (threads=$THREADS)"
"$MD_BIN" extract \
  -@ "$THREADS" \
  -o "$PREFIX" \
  -q 30 \
  -p 20 \
  --OT 0,0,0,0 \
  --OB 0,0,0,0 \
  "$REF" \
  "$BAM" \
  2>"$OUT_DIR/methyldackel.extract.log" || {
    log "MethylDackel extract failed; see $OUT_DIR/methyldackel.extract.log"
    exit 1
  }

export COMPARE_MD_OUT_DIR="$OUT_DIR"
export COMPARE_MD_SAMPLE_ID="$SAMPLE_ID"
export COMPARE_MD_BIN="$MD_BIN"
export COMPARE_MD_BAM="$BAM"
export COMPARE_MD_REF="$REF"

python3 <<'PY'
import json, os, shutil, subprocess
from datetime import datetime, timezone
from pathlib import Path

out = Path(os.environ["COMPARE_MD_OUT_DIR"])
sample_id = os.environ["COMPARE_MD_SAMPLE_ID"]
md_bin = os.environ["COMPARE_MD_BIN"]
bam = os.environ["COMPARE_MD_BAM"]
ref = os.environ["COMPARE_MD_REF"]
ver = "unknown"
try:
    p = subprocess.run([md_bin, "--version"], capture_output=True, text=True, check=False)
    text = (p.stdout or p.stderr or "").strip()
    if text:
        ver = text.splitlines()[0]
except OSError:
    pass
artifacts = sorted(
    p.name
    for p in out.iterdir()
    if p.is_file() and p.suffix in {".bedGraph", ".txt", ".tsv", ".log", ".json"}
)
payload = {
    "schema": "methylpipeline.extract_methyldackel",
    "schema_version": "1.0.0",
    "sample_id": sample_id,
    "tool": "MethylDackel",
    "tool_version": ver,
    "tool_path": shutil.which(md_bin) or md_bin,
    "bam": bam,
    "reference": ref,
    "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "artifacts": artifacts,
    "note": (
        "Comparison-only extract. Production uses MethylExtractor "
        "(sample.methyl_extract) or methylGrapher MethylCall."
    ),
}
path = out / "methyldackel_manifest.json"
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(path)
PY

log "wrote $OUT_DIR/methyldackel_manifest.json"
log "juxtapose bedGraphs vs MethylExtractor H5/manifest in comparison report"
