#!/usr/bin/env bash
# Side-by-side SamplePrep canary: linear (MojoFq2bamMeth) vs pangenome_wgbs on one sample.
# Stakeholder demo: same FASTQs, two modes, portable align_device=auto.
#
# Usage:
#   SAMPLE_ID=HBCST-042525-95676 bash scripts/sampleprep_dual_align_canary.sh
#
# Does not start DB workflows by default — prints the two methyl-study-start / overlay
# recipes operators should launch (or dry-run layout).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAMPLE_ID="${SAMPLE_ID:?set SAMPLE_ID}"
SAMPLE_ROOT="${SAMPLE_ROOT:-/work/samples/${SAMPLE_ID}}"
DEVICE="${ALIGN_DEVICE:-auto}"

echo "== dual Align canary =="
echo "sample=${SAMPLE_ID}"
echo "sample_root=${SAMPLE_ROOT}"
echo "align_device=${DEVICE}"
echo
echo "Arm A — pangenome_wgbs (recommended default)"
echo "  procedure: buffy_wgbs_pangenome_gene_fc"
echo "  actionConfig.methylgrapher_wgbs.align_device=${DEVICE}"
echo "  actionConfig.methylgrapher_wgbs.engine=mojo"
echo
echo "Arm B — linear MojoFq2bamMeth (legacy path, portable GPU)"
echo "  procedure: buffy_wgbs_linear_mojo_gene_fc"
echo "  actionConfig.parabricks.engine=mojo"
echo "  actionConfig.parabricks.align_device=${DEVICE}"
echo
echo "Compare after both SamplePrep instances finish:"
echo "  # Align wall times from node_execution started/ended"
echo "  # Optional Clara rollback arm: engine=parabricks then:"
echo "  python ${ROOT}/scripts/compare_mojo_fq2bam_vs_clara.py \\"
echo "    --clara-dir ${SAMPLE_ROOT}/linear_clara \\"
echo "    --mojo-dir ${SAMPLE_ROOT}/linear_mojo \\"
echo "    --sample-id ${SAMPLE_ID}"
echo
echo "Leadership brief: ${ROOT}/docs/architecture/mojo-multi-gpu-dual-align.md"
