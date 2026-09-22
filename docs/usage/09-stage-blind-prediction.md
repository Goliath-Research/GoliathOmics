# Stage: Blind Prediction

## Purpose

Run inference on newly arrived unlabeled samples using frozen model artifacts.

## Important boundary

Blind-only prediction is a standalone `methyl-predictor` operation.  
It is intentionally outside `methyl-validation` accuracy workflows.

## Entry command (standalone)

```bash
source .venv/bin/activate
methyl-predictor --project /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/monte_carlo_runs/production/project.json
```

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

*Blind prediction stage*



## Required config pattern

Profile `actionConfig.predictor` should reference blind/test-blind inputs and model paths resolved from frozen production project.

## Expected outputs

- blind prediction report JSON
- per-sample class probabilities and predicted subgroup labels

## Success checks

- predictor completes without missing model-path errors.
- expected blind report file exists.
- per-sample prediction rows match input sample count.

## Do not do this

- Do not use blind-only prediction results as labeled validation accuracy claims.
- Do not run blind-only predictor settings through `methyl-validation` MC paths.

## Recovery

- If model path resolution fails, confirm `production/project.json` and classifier artifacts exist.
- If sample-id join fails, verify sample path naming and predictor input lists.

## See also

- `packages/methylpredictor/docs/USAGE.md`
- `docs/usage/index.md`
