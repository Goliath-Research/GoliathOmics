# Validation planner capabilities

Middle-tier / worker contract for Monte Carlo and stratified validation runs. The workflow **engine** does not plan iterations; an optional **planner** runs before `sp_start_workflow_instance` and merges output into `context_json`.

## Workflow definition

Use **`ValidationPipeline`** (DomainProgram [`validation_model_mc.program.json`](../domain/fixtures/validation_model_mc.program.json), deployed via `scripts/deploy_workflow_definitions.sh`):

- `FOREACH` over `context_json.iterations[]` (sequential by default)
- Each iteration runs centroid → detector with `${var.taskConfig}` in templates
- After all iterations, `final_sequence` runs mapper → enricher → progression on `projectPath`

Example instance payload: [`mc_two_group_test.json`](../sql_pg/instance_context_examples/mc_two_group_test.json).

## Capability (implemented)

| Field | Value |
|-------|-------|
| Capability id | `validation.plan-iterations` |
| Worker handler | `workers/methyl_worker/handlers.py` → `_handle_validation_plan_iterations` |
| Core library | `packages/methylvalidation/methyl_validation/workflow_planner.py` |
| REST | `POST /v1/validation/plan-iterations` (Python gateway) |
| DB apply | `wf.wf_apply_validation_plan` — merge `context_json` + optional `instance_extension` |
| CLI | `methyl-worker plan-iterations --plan-input request.json` |

Invoked by portal/middle-tier before instance start, or as a workflow ACTION when a worker registers `validation.plan-iterations`.

## Input

JSON body (planner request):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `projectPath` | string | yes | Base project JSON path or resolved project root |
| `featureIterations` | int | no | Feature-stability MC count (default from profile/site `actionConfig.validation` via `resolvedConfig`) |
| `qualityIterations` | int | no | Post-model quality MC count |
| `seed` | int | no | RNG seed for reproducible splits |
| `trainFraction` | number | no | Stratified train fraction (e.g. `0.8`) |
| `layout` | string | no | `binary` or multiclass layout name |

The planner reads merged `actionConfig.validation` (profile + site + instance `context_json`) and `validation.schema.json` (`validation_monte_carlo`) when overrides are omitted.

## Output

Merge into instance `context_json`:

```json
{
  "projectPath": "/work/.../Plasma_healthy_vs_PCa",
  "workerToolMapper": "MethylMapper",
  "workerToolEnricher": "MethylEnricher",
  "workerToolProgression": "MethylDiseaseProgression",
  "orderedComparisonLabels": ["healthy_vs_PCa"],
  "centroidSeedGroups": [
    {
      "$type": "CentroidSeedGroup",
      "label": "__control__",
      "addSamples": ["/work/samples/ctrl1", "/work/samples/ctrl2"],
      "removeSamples": [],
      "centroidDir": "/work/.../monte_carlo_runs/_centroid_seed/__control__"
    }
  ],
  "iterations": [
    {
      "$type": "StratifiedCohortDraw",
      "runId": "feature_run_0001",
      "phase": "feature",
      "projectPath": "/work/.../monte_carlo_runs/run_0001",
      "centroidGroups": [
        {
          "label": "__control__",
          "addSamples": [],
          "removeSamples": ["/work/samples/ctrl2"],
          "centroidDir": "/work/.../monte_carlo_runs/run_0001/centroids/__control__",
          "centroidSeedDir": "/work/.../monte_carlo_runs/_centroid_seed/__control__"
        }
      ],
      "taskConfig": {
        "runId": "feature_run_0001",
        "phase": "feature",
        "iteration": 1,
        "layout": "binary",
        "trainFraction": 0.8,
        "seed": 42
      }
    }
  ]
}
```

### Parallel centroid seed (default)

When `validation.parallel_mc_centroid_seed` is unset or `true` (profile/site `actionConfig.validation`):

1. **`centroidSeedGroups[]`** — one entry per MC cohort label with the **full** resolved sample pool; centroids materialize under `{monteCarloRunsRoot}/_centroid_seed/{label}/`.
2. DomainPrograms run a **parallel** FOREACH over `centroidSeedGroups`, then a **parallel** FOREACH over `iterations`.
3. Each iteration’s `centroidGroups[]` uses **cohort-relative** deltas (`removeSamples = full_cohort \ train`) and `centroidSeedDir` pointing at the shared seed tree. Workers copy the seed baseline into the run `centroidDir` before `methyl-centroid` applies deltas.

Set `parallel_mc_centroid_seed: false` to restore legacy sequential chaining via `previousRunDir` / `prepare_incremental_centroid_baseline`.

### `iterations[]` element shape

Each object is opaque to the engine except for fields referenced in workflow templates:

| Field | Type | Used by ValidationPipeline |
|-------|------|---------------------------|
| `runId` | string | `${var.runId}` |
| `phase` | string | `${var.phase}` |
| `projectPath` | string | `${var.projectPath}` (iteration-specific run project) |
| `taskConfig` | object | `${var.taskConfig}` embedded JSON in centroid/detector payloads |
| `centroidGroups` | array | Per-group `centroidDir`, `centroidSeedDir`, `addSamples`, `removeSamples` for `pipeline.centroid` |
| `previousRunDir` | string | Legacy sequential MC only (`parallel_mc_centroid_seed: false`) |

Top-level **`centroidSeedGroups`** drives the seed-phase FOREACH in DomainProgram MC workflows (`parallel: true`).

FOREACH flattening copies object keys into scope (`wf_seed_foreach_iteration_scope`), so `${var.taskConfig}` resolves without engine injection.

## Optional audit persistence

Planners may persist the full plan for debugging:

```sql
EXEC wf.wf_repo_upsert_instance_extension
  @instance_id = @id,
  @extension_key = N'methylvalidation.plan',
  @data_json = @plan_json;  -- json / jsonb
```

Read back:

```sql
SELECT wf.wf_repo_get_instance_extension(@id, N'methylvalidation.plan');
```

This is **not required** for execution — only `context_json.iterations[]` drives the workflow.

## Portal integration

1. Call planner (`validation.plan-iterations`) with base project parameters.
2. Merge planner `iterations[]` (and any top-level keys) into `context_json`.
3. Create workflow instance bound to **`ValidationPipeline`** (not `MethylValidationFlow`).
4. `EXEC wf.sp_start_workflow_instance @workflow_instance_id`.

See also: [`packages/methylvalidation/docs/USAGE.md`](../../packages/methylvalidation/docs/USAGE.md), [end-to-end workflow architecture](../../docs/architecture/end-to-end-workflow.md).
