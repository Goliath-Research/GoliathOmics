# methylextractionqc Usage

Read MethylExtractor `{sample_id}.extraction_manifest.json`, evaluate Pass/Fail guardrails, and write `{sample_id}.extraction_qc.json` in the sample directory.

MethylExtractor exports metrics at extraction time; this package owns post-extraction QC decisions for MethylPipeline workers.

Upstream contract: MethylExtractor `docs/extraction_qc_contract.md` (when available in the extractor repo).

Operator guide: [Usage ch.03 — Sample Prep and QC](../../docs/usage/03-sample-prep-and-qc.md).

## CLI

```bash
methyl-extraction-qc --sample-dir /work/samples/S1 --sample-id S1
methyl-extraction-qc --project /work/projects/prostate-cancer/configs/project_Example.json
```

## Guardrails (defaults)

| Guardrail | Source field | Default |
|-----------|--------------|---------|
| CpG mean coverage | `summary.cpg_weighted_mean_coverage` | ≥ 10 |
| CHH methylation | `summary.chh_methylation_level` | ≤ 0.02 (when CHH extracted) |
| CHG methylation | `summary.chg_methylation_level` | ≤ 0.02 (when CHG extracted) |
| Chromosome completeness | `per_chromosome` vs expected set | all present with CG |
| Chromosome uniformity | autosomal `CG.mean_coverage` min/median | ≥ 0.5 |

Override thresholds via profile or site `actionConfig.extraction_qc`.

## Output

- `{sampleDir}/{sampleId}.extraction_qc.json` — guardrail report with `guardrails.overall_pass`
- JSON Schema: [`schemas/extraction_qc_output.schema.json`](../../schemas/extraction_qc_output.schema.json) (`methylpipeline.extraction_qc` v1.0.0)

## Workflow integration

SamplePrepPipeline runs `sample.extraction_qc` after `sample.methyl_extract`. Scope variable `extractionQcPass` comes from `guardrails.overall_pass`.

See [`workflow_engine/sql_mssql/SamplePrepFlow.md`](../../workflow_engine/sql_mssql/SamplePrepFlow.md) and [`workflow_engine/contract/sample_prep_capabilities.md`](../../workflow_engine/contract/sample_prep_capabilities.md).

## Related

- Theory: [`docs/theory/chapters/09a-methylextractionqc.md`](../../docs/theory/chapters/09a-methylextractionqc.md)
- Alignment QC (upstream): [`packages/methylalignmentqc/docs/USAGE.md`](../methylalignmentqc/docs/USAGE.md)
