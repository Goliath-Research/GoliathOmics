#!/usr/bin/env bash
# Bootstrap local fixtures for SamplePrep smoke (WORKER_STUB_EXTERNAL=1 + real methyl_qc).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_ROOT="${SMOKE_RUN_ROOT:-$REPO_ROOT/.smoke/sample_prep}"
SAMPLE_ID="${SMOKE_SAMPLE_ID:-smoke-1}"
FIXTURE_DIR="$REPO_ROOT/packages/methylalignmentqc/data/003772_8C9_3"

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap_sample_prep_smoke_fixtures.sh [options]

Options:
  --run-root PATH     Smoke run root (default: .smoke/sample_prep under repo)
  --sample-id ID      Sample id (default: smoke-1)
  -h, --help          Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-root) RUN_ROOT="${2:-}"; shift 2 ;;
    --sample-id) SAMPLE_ID="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -d "$FIXTURE_DIR" ]] || { echo "Missing fixture dir: $FIXTURE_DIR" >&2; exit 1; }

FASTQ_DIR="$RUN_ROOT/fastq/$SAMPLE_ID"
SAMPLE_DIR="$RUN_ROOT/samples/$SAMPLE_ID"
OUT_BASE="$RUN_ROOT/out"
PROJECT_PATH="$RUN_ROOT/project.json"
REF_FASTA="${SMOKE_REFERENCE_FASTA:-/work/genomes/linear/GRCh38/ensembl-116/Homo_sapiens.GRCh38.dna.primary_assembly.fa}"

mkdir -p "$FASTQ_DIR" "$SAMPLE_DIR" "$OUT_BASE/alignment_qc"

# Placeholder FASTQs (download may copy from fastqStorage; stubs also touch these).
: > "$FASTQ_DIR/${SAMPLE_ID}_1.fastq.gz"
: > "$FASTQ_DIR/${SAMPLE_ID}_2.fastq.gz"

cp "$FIXTURE_DIR/003772_8C9_3.json" "$SAMPLE_DIR/${SAMPLE_ID}.json"
if [[ -f "$FIXTURE_DIR/003772_8C9_3.deduplicate_metrics.txt" ]]; then
  cp "$FIXTURE_DIR/003772_8C9_3.deduplicate_metrics.txt" "$SAMPLE_DIR/${SAMPLE_ID}.deduplicate_metrics.txt"
fi

# Skip Parabricks when WORKER_STUB_EXTERNAL=1 or when outputs already exist.
: > "$SAMPLE_DIR/${SAMPLE_ID}.bam"

ARCHIVE_ROOT="$RUN_ROOT/archive"
mkdir -p "$ARCHIVE_ROOT"

cat > "$SAMPLE_DIR/${SAMPLE_ID}.extraction_manifest.json" <<'MANIFEST'
{
  "metadata": {
    "schema_name": "methylextractor.extraction_manifest",
    "schema_version": "1.0.0",
    "contexts_extracted": ["CG"]
  },
  "summary": {"cpg_weighted_mean_coverage": 20.0},
  "per_chromosome": {"21": {"CG": {"mean_coverage": 18.0}}}
}
MANIFEST

printf 'stub-h5' > "$SAMPLE_DIR/21-CG.h5"

cat > "$PROJECT_PATH" <<EOF
{
  "project_name": "SamplePrep_smoke",
  "output_base": "$OUT_BASE",
  "samples_base_path": "$RUN_ROOT/samples",
  "chromosomes": ["21"],
  "contexts": ["CG"],
  "controls": {
    "label": "healthy",
    "groups": [{"label": "all", "sample_paths": []}]
  },
  "diseases": {
    "label": "cancer",
    "groups": [{"label": "PCa", "sample_paths": []}]
  },
  "step_config": {
    "alignment_qc": {
      "genome_fasta": "$REF_FASTA",
      "output_dir": "$OUT_BASE/alignment_qc"
    },
    "methyl_extract": {
      "extract_contexts": ["CG"],
      "threads": 1,
      "output_format": "hdf5"
    }
  }
}
EOF

cat > "$RUN_ROOT/smoke_env.sh" <<EOF
export SMOKE_RUN_ROOT="$RUN_ROOT"
export SMOKE_SAMPLE_ID="$SAMPLE_ID"
export SMOKE_PROJECT_PATH="$PROJECT_PATH"
export SMOKE_FASTQ_BASE="$RUN_ROOT/fastq"
export SMOKE_SAMPLE_DIR="$SAMPLE_DIR"
export SMOKE_ARCHIVE_BASE="$ARCHIVE_ROOT"
EOF

echo "Bootstrap OK: run_root=$RUN_ROOT sample=$SAMPLE_ID"
echo "  project: $PROJECT_PATH"
echo "  sample dir: $SAMPLE_DIR"
echo "  fastq base: $FASTQ_DIR"
echo "  archive base: $ARCHIVE_ROOT"
