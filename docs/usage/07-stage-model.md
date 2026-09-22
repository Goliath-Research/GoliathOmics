# Stage: Model

## Purpose

Train and select final production model artifacts after freeze and biological-readiness checks.

> **Canonical entry:** [Orchestration (workflow-run)](04-orchestration-workflow-run.md). Model training and selection are steps in `study_validation_lifecycle.program.json` (or portal/gateway instances compiled from the same programs). Legacy `methyl-validation --model` / `--model-mc` flags are transitional only.

## Common execution patterns

### Pattern A: Full lifecycle (canonical)

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/study_validation_lifecycle.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-5-CG.json","pipelineProfile":"mc_dmp_gene_fc"}'
```

Run biological readiness before model stages when not using the full lifecycle program:

```bash
methyl-validation biological-readiness /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG
```

### Pattern B: Direct production model build (legacy CLI)

```bash
source .venv/bin/activate
methyl-validation biological-readiness /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json --model
```

### Pattern C: Backend model-MC comparison then select best (legacy CLI)

```bash
source .venv/bin/activate
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json --model-mc --model-mc-all
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json \
  --select-best-model --model-mc-all --selection-metric balanced_accuracy --selection-stat median
```

### Pattern D: Model-MC only with strict artifact reuse (canonical)

Use the slim program when stability/freeze already completed and model-MC must not
recompute centroids or detections:

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/validation_model_mc.program.json \
  --context-file /work/projects/prostate-cancer/configs/context_H_PCa_good_ecdf_covariates.json \
  --parallel-workers 1 -v
```

This graph contains only `validation.model_mc`. It requires every primary
`monte_carlo_runs/run_XXXX/` split to have a compatible split plus `project.json`,
`centroids/`, and `detections/`. Missing or incompatible artifacts fail the action;
centroid/detector fallback is disabled. Enabled backends and covariates come from
`actionConfig.validation.backend_profiles` in the resolved context.

With `n_iterations: 10` and ECDF enabled, outputs are written under
`monte_carlo_runs/model_mc/shared/run_0001` … `run_0010` and
`monte_carlo_runs/model_mc/ecdf/run_0001` … `run_0010`, with
`ecdf/metrics_summary.json`, `ecdf/all_metrics.csv`, and the model-MC manifest.
Each backend run writes `train_metrics.json` for diagnostics and
`test_metrics.json` for the disjoint held-out partition. Only test metrics feed
`all_metrics.csv`, summaries, and backend ranking. Canonical split files are
`test_control.csv`, `test_disease.csv`, and `test_groups.json`. Tabular and generative backends use the same
`evaluation_partition` contract as ECDF (not legacy predictor `test_*` cohort
lists), so shared-run train/test membership stays comparable across backends.
Tabular **train-time** `test_dataset.h5` export uses the same rule:
`evaluation_partition="test"` → `test_groups.json` only (fail closed if missing).
Misnamed predictor `test_control_paths` / `test_disease_paths` must never fill
the holdout design matrix.

Every sum-to-1 feature set (a simplex) is encoded as: binary (\(K=2\)) → closed
non-reference probability \(p\); multiclass (\(K>2\)) → additive log-ratio (ALR)
vs the reference (drops the redundant part). First-stage ECDF class
probabilities follow the same rule (binary stacks readable `prob_class1` in
\([0,1]\); multiclass uses ALR and requires
`ecdf_second_stage_probability_epsilon`). Cell-type fractions and other declared
composition groups emit one \(p_*\) column when \(K=2\), or \(K-1\) `alr_*`
coordinates when \(K>2\). For ECDF with six-part leukocyte covariates the
second-stage LR therefore uses six independent features: binary `prob_class1`
plus five standardized Neu-referenced ALR fractions (`CD8T`, `CD4T`, `NK`,
`Bcell`, `Mono`). Declare composition groups with
`covariate_composition_groups` (see the covariate contract in the
[MethylValidation usage guide](../../packages/methylvalidation/docs/USAGE.md)).
The exact matrices are saved under each run's `model_bundle/` as
`train_dataset.h5` / `test_dataset.h5` (float32 features, plus
`dataset_manifest.json`); the manifest records transform parameters and verifies
zero train/test sample overlap.

## Backend behavior

- `ecdf`: `methyl-classifier` -> `methyl-predictor`
- `tabular_sklearn`: in-process bundle -> train -> predict
- `generative_hybrid`: in-process bundle -> train -> predict

For `ecdf` + observed-hybrid mapped families (`feature_mode=observed_hybrid`, `feature_family_set!=dmp_scored`), the runtime path is aggregated ECDF OvR package train/predict and skips ECDF second-stage refinement.

Configured by profile `actionConfig.validation.backend_profiles` (plus optional CLI backend override).

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

*Model stage in pipeline lifecycle*



## Required config keys

- `backend_profiles`
- backend-specific `params` under each profile (for example `feature_mode`, `ecdf_second_stage_enabled`, `tabular_methods`, `generative_latent_dim`)
- observed-hybrid family contract (when used):
  - `feature_family_set`
  - mapper annotation cache from freeze (`production/model_bundle/mapper_dmp_annotations.csv`)
- tabular controls:
  - `tabular_methods`
  - `tabular_method_selection_metric`
  - optional `tabular_max_dmps` (null/0 keeps all stable loci)
- generative controls:
  - `generative_latent_dim`, `generative_epochs`, and related keys
- optional gate:
  - `require_biological_review_for_model`
  - `biological_review_confirmed`

Legacy flat backend keys are rejected. Migrate older configs with:

`methyl-validation-migrate-backend-config /path/to/project.json --in-place`

## Expected outputs

- `<mc_root>/production/classifiers/...`
- `<mc_root>/production/predictors/...`
- aggregated ECDF mode writes:
  - `<mc_root>/production/classifiers/ecdf_aggregated_ovr.pkl`
  - `<mc_root>/production/classifiers/ecdf_aggregated_ovr.meta.json`
- if model-MC:
  - `<mc_root>/model_mc/shared/run_XXXX/...`
  - `<mc_root>/model_mc/<backend>/all_metrics.csv`
  - `<mc_root>/model_mc/backend_ranking.csv`
  - `<mc_root>/production/selected_backend.json` (after selection)

When model-MC reuses primary split artifacts, shared/backend run roots may link centroid/detector directories instead of recomputing them.

## Success checks

- final classifier/predictor artifacts exist in production root.
- selected backend metadata exists when using selection workflow.
- metrics summaries include expected selection metric fields.
- the last biological-readiness report is `go`/`go_with_risks` and reviewed by humans.

## Do not do this

- Do not run model before freeze.
- Do not skip biological readiness review when production artifacts changed.
- Do not claim backend superiority without model-MC summaries.

## See also

- `docs/theory/chapters/15-model-creation-and-validation.md`
- `packages/methylvalidation/docs/ROLLOUT.md`
