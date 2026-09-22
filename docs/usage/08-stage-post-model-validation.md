# Stage: Post-model Validation

## Purpose

Measure descriptive performance distributions using frozen production artifacts without retraining.

> **Canonical entry:** Post-model validation is a step in `study_validation_lifecycle.program.json`. Legacy `methyl-validation --post-model-validation` is transitional only. See [Orchestration](04-orchestration-workflow-run.md).

## Entry command (canonical)

Run as part of the full lifecycle program, or re-run the lifecycle graph after freeze/model — completed actions skip via `.action_results/` manifests.

## Legacy entry command

```bash
source .venv/bin/activate
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json --post-model-validation
```

Optional backend override (legacy CLI):

```bash
source .venv/bin/activate
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json \
  --post-model-validation --post-model-backend ecdf
```

## Prerequisites

- Freeze/model artifacts already built in production root.
- Valid `production/project.json`.

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

*Post-model validation*



## Expected outputs

Canonical one-shot DomainProgram action:

- `<mc_root>/post_model_validation/predictors/...`
- `<mc_root>/post_model_validation/logs/...`
- `<mc_root>/post_model_validation/post_model_validation_report.json`

The transitional CLI retains its repeated-evaluation layout:

- `<mc_root>/post_model_validation/run_XXXX/...`
- `<mc_root>/post_model_validation/all_metrics.csv`
- `<mc_root>/post_model_validation/metrics_summary.json`
- `<mc_root>/post_model_validation/metrics_distributions_plotly.html`

Historical `run_0001` directories are not moved or deleted by the one-shot action.

## Success checks

- One-shot workflow: `post_model_validation_report.json` and predictor metrics exist directly under `post_model_validation/`.
- Legacy repeated CLI: metrics summary includes key metrics (`balanced_accuracy`, `macro_f1`, plus available calibration metrics), and the distribution plot is generated.

## Do not do this

- Do not treat post-model validation as model training.
- Do not compare candidate and baseline manually without fixed thresholds; use rollout comparison when promoting.

## Recovery

- Resume interrupted post-model validation:

```bash
source .venv/bin/activate
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json \
  --post-model-validation --resume
```

## See also

- `packages/methylvalidation/docs/USAGE.md`
- `packages/methylvalidation/docs/ROLLOUT.md`
