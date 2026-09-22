# Stage: Freeze

## Purpose

Create frozen production artifacts using the stable panel and generate biological interpretation outputs (mapper/enricher, optional progression).

> **Canonical entry:** [Orchestration (workflow-run)](04-orchestration-workflow-run.md). After stability, run a freeze DomainProgram (e.g. `pca1_5_freeze.program.json`) or the full `study_validation_lifecycle.program.json`. Legacy `methyl-validation --freeze` is transitional only.

## Entry command (canonical)

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/validation_freeze.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-5-CG.json","pipelineProfile":"mc_dmp_gene_fc"}'
```

## Legacy entry command

```bash
source .venv/bin/activate
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json --freeze
```

## Prerequisites

- Stability stage completed.
- Stable panel available at:
  - configured profile `actionConfig.validation.freeze_stable_dmp_csv`, or
  - default `<mc_root>/stability/stable_dmps_production.csv`.

## Process model

```mermaid
flowchart LR
  prep["Sample prep FASTQ to HDF5"]
  qc["Alignment + extraction QC"]
  stability["MC stability centroid detector"]
  freeze["Freeze fixed panel mapper enricher"]
  model["Model train + predictor"]
  validation["Post-model validation"]
  blind["Blind prediction"]

  prep --> qc --> stability --> freeze --> model --> validation --> blind
```

*Freeze stage in pipeline lifecycle*



## Expected outputs

- `<mc_root>/production/project.json`
- `<mc_root>/production/production_summary.json`
- `<mc_root>/production/model_bundle/mapper_dmp_annotations.csv` (when mapped-family feature bundles are prepared)
- mapper outputs under `production/mapper/...`
- enricher outputs under `production/enricher/...`
- optional progression outputs under `production/progression/...`

## Success checks

- `production/project.json` exists and includes fixed panel usage for detector.
- expected mapper and enricher files are present.
- stage-specific logs show successful return codes.
- for non-`dmp` observed-hybrid families, `production_summary.json` includes `mapper_annotation_cache` metadata.

## Recommended post-freeze gate

Run biological readiness before model training:

```bash
source .venv/bin/activate
methyl-validation biological-readiness /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG
```

This chained gate verifies:

- enricher completeness (`verify-complete`),
- strict progression input completeness (`--strict-missing`),
- stability/freeze/progression readiness verdict.

To include advisory Grok commentary in the readiness section:

```bash
methyl-validation biological-readiness /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG --grok-review
```

Without `--grok-review`, the chain keeps Grok disabled by default and still enforces deterministic checks.

## Do not do this

- Do not run freeze before stability panel exists.
- Do not delete `production/project.json`; later model and predictor stages depend on it.

## Recovery

- If freeze fails because stable panel missing, rerun stability or set `freeze_stable_dmp_csv`.
- If path mismatch occurs across machines, use `path_remap` or `--path-remap`.

## See also

- `packages/methylvalidation/docs/USAGE.md`
- `docs/usage/index.md`
