# PCa1-5 CG workflow check bundle

DomainProgram fixtures and harness for `Healthy_vs_PCa1-5-CG` (hierarchical multiclass, buffy coat, CG context).

**Layout:** `configs/*.program.json` and profiles under `workflow_engine/domain/profiles/` are **repo** artifacts. Production `project_*.json` and sample CSVs live on **`/work/projects/prostate-cancer/`** only; smoke copies here are for CI.

## Contents

| Path | Purpose |
|------|---------|
| `configs/project_Healthy_vs_PCa1-5-CG.json` | Full project config (24 chr, 30 MC iterations) |
| `configs/project_Healthy_vs_PCa1-5-CG_smoke.json` | Smoke config (chr 21, 2 iterations) |
| `configs/pca1_5_mc_stability*.program.json` | MC + stability workflows |
| `configs/pca1_5_freeze.program.json` | Production freeze (granular centroids/detectors) |
| `configs/pca1_5_model.program.json` | Model selection + post-model validation |
| `../../fixtures/full_lifecycle.program.json` | End-to-end composed pipeline (includes `validation.model_mc`) |
| `instance/context*.json` | Workflow instance payloads |
| `check_pipeline.py` | Validate project, compile program, check templates |

## Quick check

```bash
source .venv/bin/activate
python workflow_engine/domain/checks/pca1_5_cg/check_pipeline.py
python workflow_engine/domain/checks/pca1_5_cg/check_pipeline.py \
  --program workflow_engine/domain/fixtures/mc_stability_smoke.program.json
```

## Centroid efficiency and parallel MC

`validation.plan_iterations` materializes **`centroidSeedGroups`** (full cohort pools under `_centroid_seed/`) and per-iteration **`centroidGroups`** with cohort-relative `addSamples` / `removeSamples` and `centroidSeedDir`. MC DomainPrograms run a **parallel seed FOREACH** over `centroidSeedGroups`, then **parallel iterations** (`parallel: true` on the outer `iterations` loop).

Deploy updated graphs: `bash scripts/deploy_mc_workflow_definitions.sh` (see [`docs/plans/parallel-mc-centroid-seed.plan.md`](../../../../docs/plans/parallel-mc-centroid-seed.plan.md)).

Legacy sequential chaining (`previousRunDir`, incremental baseline copy) remains available when `validation.parallel_mc_centroid_seed: false` in profile/site config.

`pipeline.centroid` passes wire fields via `CentroidTaskInput`; workers copy the seed baseline before `methyl-centroid` applies deltas.
