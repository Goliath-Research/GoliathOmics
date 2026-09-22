#!/usr/bin/env python3
"""
Backfill full V2 sample QC JSON (all guardrail layers) from an on-disk sample folder.

Use on legacy samples that were aligned before methyl_qc ran in the pipeline.
Reads Picard deduplicate metrics, Parabricks/Picard JSON (or qc-metrics.tar), optional
BAM for samtools flagstat, and optional bisulfite sidecar files.

Examples:
  source .venv/bin/activate

  # Core sequencing guardrails only (WGBS Parabricks + cycle screening)
  python scripts/methyl_sample_qc_backfill.py /work/samples/003772_8C9_3

  # Full analyte profile (alignment + fragmentomics + bisulfite + flagstat when BAM present)
  python scripts/methyl_sample_qc_backfill.py /work/samples/003772_8C9_3 \\
    --analyte cfdna -o /tmp/003772_8C9_3.sample_qc.json

  # Same via installed entry point
  methyl-sample-qc-backfill /work/samples/003772_8C9_3 --analyte cfdna

  # Project step_config (same as pipeline methyl_qc)
  python scripts/methyl_sample_qc_backfill.py /work/samples/003772_8C9_3 \\
    --project /work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json

  # JSON to stdout only (no file)
  python scripts/methyl_sample_qc_backfill.py /work/samples/003772_8C9_3 --analyte cfdna --stdout

  # File and stdout together
  python scripts/methyl_sample_qc_backfill.py /work/samples/003772_8C9_3 \\
    --analyte cfdna -o /tmp/out.json --stdout
"""

from methyl_alignment_qc.cli.sample_qc_backfill import main

if __name__ == "__main__":
    raise SystemExit(main())
