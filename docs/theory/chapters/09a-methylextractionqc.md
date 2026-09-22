# MethylExtractionQC {#sec-methylextractionqc}
## Role

`methylextractionqc` evaluates the extraction manifest after methylation extraction. For linear / stock pangenome that is **CPU MethylExtractor** (C/HTSlib/HDF5); for `pangenome_wgbs` it is **native-Mojo** methylGrapher `MethylCall`/`MergeCpG` (CPU-parallel Mojo hot path — not stock Python and not a CUDA/HIP GPU workload). Alignment is the GPU-bound step. Alignment QC confirms the QC BAM / GAF provenance; extraction QC confirms the methylation call layer has sufficient coverage and conversion-quality signal before HDF5 files enter downstream cohort analysis.

Operator guide: [Usage ch.03 — Sample Prep and QC](../../usage/03-sample-prep-and-qc.md).

## Input: extraction manifest

MethylExtractor or methylGrapher (`sample.methylgrapher_wgbs_extract`) writes `{sample_id}.extraction_manifest.json` with the **same canonical blocks** consumed by `methylextractionqc`:

- `metadata` — `schema_name`, `schema_version`, `contexts_extracted`, `sample_id` (methylGrapher adds graph provenance sidecars)
- `summary` — genome-wide CpG weighted mean coverage, CHH/CHG methylation levels
- `per_chromosome` — per-chromosome `CG.mean_coverage` and related stats
- `read_filtering` — aggregated read filter stats when available (`reads_seen`, `reads_used`, `read_retention_rate`, per-reason drop counts). **Omitted on methylGrapher graph extract** — the discard-fraction guardrail reports a skipped check rather than inventing data.

## Guardrails

`methylextractionqc/guardrails.py` evaluates Pass/Fail checks (defaults):

| Guardrail | Default | Interpretation |
|-----------|---------|----------------|
| `cpg_weighted_mean_coverage` | ≥ 10× | Sufficient read depth at CpG sites |
| `chh_methylation_level` | ≤ 0.02 | Conversion sanity (when CHH extracted) |
| `chg_methylation_level` | ≤ 0.02 | Same for CHG |
| `chromosome_completeness` | all expected chromosomes | Every project chromosome present with CG block |
| `chromosome_uniformity` | autosomal min/median ≥ 0.5 | No severe localized dropout |
| `read_discard_fraction` | ≤ 0.9 | Catastrophic read loss (`1 − read_retention_rate`) — wrong reference, contamination, or mis-set filters |

The `read_discard_fraction` check reads the manifest `read_filtering` block; when that block is absent (manifests predating read-filtering stats) the check is skipped as a pass so older samples are not retroactively failed.

`guardrails.overall_pass` maps to workflow **`extractionQcPass`**. Failures are terminal in SamplePrep today (no automatic retry).

Output: `{sample_id}.extraction_qc.json` (schema: `schemas/extraction_qc_output.schema.json`).

## Configuration

Override thresholds (including `max_discard_fraction`) via profile/site `actionConfig.extraction_qc.guardrails`.

## Why both alignment and extraction QC

| Question | Answered by |
|----------|-------------|
| Did sequencing/alignment produce a usable BAM / GAF provenance? | Alignment QC (`methyl-qc`, mode-aware) |
| Did extraction yield enough confident methylation signal? | Extraction QC (`methyl-extraction-qc`) |

A sample may pass alignment but fail extraction (coverage collapse). Conversely, remediation after alignment QC avoids extraction on alignments that will not support stable statistics.

## Publication guidance

Describe extraction QC as deterministic threshold checks on exporter-reported coverage and non-CpG methylation fractions — not a new methylation estimator.

## Related

- Package usage: `packages/methylextractionqc/docs/USAGE.md`
- Alignment QC (upstream gate): [§ methylalignmentqc](09-methylalignmentqc.md#sec-methylalignmentqc)
