# methylextractionqc

Read MethylExtractor `{sample_id}.extraction_manifest.json` (and optional per-context sidecars), evaluate Pass/Fail guardrails, and write `{sample_id}.extraction_qc.json` in the sample directory.

MethylExtractor exports metrics at extraction time; this package owns post-extraction QC decisions for MethylPipeline workers.

See MethylExtractor `docs/extraction_qc_contract.md` for the upstream JSON contract.

## CLI

```bash
methyl-extraction-qc --sample-dir /work/samples/S1 --sample-id S1
methyl-extraction-qc --project project.json
```

## Guardrails (defaults)

| Guardrail | Source | Default |
|-----------|--------|---------|
| CpG mean coverage | `summary.cpg_weighted_mean_coverage` | ≥ 10 |
| CHH methylation | `summary.chh_methylation_level` | ≤ 0.02 (when CHH extracted) |
| CHG methylation | `summary.chg_methylation_level` | ≤ 0.02 (when CHG extracted) |
| Chromosome completeness | `per_chromosome` vs expected set | all present with CG |
| Chromosome uniformity | autosomal `CG.mean_coverage` min/median | ≥ 0.5 |

Override thresholds via profile or site `actionConfig.extraction_qc`.

Output artifact JSON Schema: [`schemas/extraction_qc_output.schema.json`](schemas/extraction_qc_output.schema.json) (`methylpipeline.extraction_qc` v1.0.0).
