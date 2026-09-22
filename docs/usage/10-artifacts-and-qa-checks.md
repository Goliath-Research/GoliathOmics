# Artifacts and QA Checks

## Purpose

Provide a single checklist for expected files at each stage and quick quality gates before proceeding.

## Workflow action observability

Each workflow ACTION executed by `methyl-worker` submits a typed `output_json` to the database (validated against `schemas/tasks/*.json`). Outputs include timing (`duration_ms`), branch code (`result_code`), and paths to artifacts on shared storage.

On `/work`, look for:

- **Per-action manifests:** `{output_dir}/.action_results/{action_name}.{run_key}.json` — full typed result written by CLIs (e.g. `pipeline.dmp_select`, `pipeline.mapper`) or reconstructed by the worker collector. Schema 1.2 adds `content_key` and `hyperparam_set_id` when [CAAS](17-content-addressed-action-store.md) is enabled.
- **Sample prep timeline:** `{sampleDir}/{sampleId}.sample_prep_log.jsonl` — append-only JSONL audit for download, align, QC, extract, archive steps.
- **Workflow action timeline:** `{logRoot}/action_run_log.jsonl` — append-only log for every workflow ACTION when a log root resolves (MC runs, project output, or sample dir). Records include timing, typed `inputs`/`outputs`, and idempotency fields.

For `.caas/` layout, hyperparameter-set ledgers, and cross-instance reuse, see [Content-Addressed Action Store (CAAS)](17-content-addressed-action-store.md).

Regenerate JSON Schema artifacts after model changes:

```bash
source .venv/bin/activate
methyl-export-task-schemas
methyl-export-task-schemas --check   # CI drift gate
```

## Artifact map

| Stage | Required artifacts | Gate to proceed |
|---|---|---|
| Sample prep | `{sample_id}.json`, `alignment_qc/{sample_id}.json`, `{sample_id}.extraction_qc.json`, `{chr}-CG.h5` | `guardrails.overall_pass` true for alignment and extraction QC |
| Stability | `stability/stable_dmps_production.csv`, `stability_summary.json`, `all_metrics.csv` | Stable panel exists and is non-empty |
| Freeze | `production/project.json`, mapper/enricher outputs | Production project exists; fixed-panel detector run completed |
| Model | `production/classifiers/*`, `production/classifiers/training_metrics.json` (all model backends), `production/predictors/*` | Final classifier/predictor artifacts present |
| Post-model validation | `post_model_validation/all_metrics.csv`, `metrics_summary.json`, plotly report | Distribution summaries generated |
| Blind prediction | blind prediction report + per-sample probabilities | All input blind samples receive predictions |

## Handoff integrity checks

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

*Artifact handoffs*



## QA checklist per stage

### Stability

- Iteration count in metrics roughly matches configured `n_iterations`.
- Stable panel size is consistent with selected thresholds.

### Freeze

- `production/project.json` references fixed panel detector mode.
- Mapper/enricher outputs exist for each expected comparison group.

### Model

- Backend used is recorded and consistent with config.
- If selection used, ranking artifacts exist and chosen backend is traceable.

### Post-model validation

- Metrics summary includes required decision metrics.
- Distribution artifacts generated and readable.

### Blind prediction

- Prediction outputs include all requested samples.
- Operational report does not include false accuracy claims.

## Escalation triggers

- Empty stable panel.
- Missing production project.
- No model artifacts after model stage.
- Repeated run failures in same step across resumed runs.

## See also

- `packages/methylvalidation/docs/USAGE.md`
- `packages/methylvalidation/docs/IMPLEMENTATION.md`
