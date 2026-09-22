# DataDrivenPipeline — generic workflow engine usage

**DataDrivenPipeline** is a **domain-neutral** workflow definition (~15 nodes). It uses the **FOREACH** control-flow node to fan out over JSON arrays in `workflow_instance.context_json`. No chromosome or comparison count is baked into the graph.

Methylation (PCa3 OvR) is one **instance payload**, not a separate workflow definition.

## Architecture

```text
Engine (generic)                    Workflow definition (generic)
─────────────────                   ───────────────────────────────
SEQUENCE / PARALLEL / IF / …        DataDrivenPipeline seed
REPEAT / WHILE / FOREACH     ←──   FOREACH comparisons (parallel)
scope_variable / placeholders       FOREACH chromosomes (parallel)
worker_action + capabilities        ACTION nodes + templates

Instance (project-specific)         Workers (domain adapters)
─────────────────────────           ───────────────────────────
context_json: comparisons[],         methyl-centroid / detector / …
  chromosomes[], paths, labels       (or any capability string)
```

## FOREACH semantics

| Column | Purpose |
|--------|---------|
| `foreach_collection_var` | Scope variable name holding a JSON **array** |
| `foreach_item_var` | Current element written each iteration (default `item`) |
| `foreach_index_var` | Zero-based index (default `index`) |
| `foreach_parallel` | `1` = fan out all iterations concurrently; `0` = sequential (like REPEAT) |

On each BODY entry, object elements are **flattened** into scope (keys become `${var.label}`, `${var.detectOutDir}`, etc.). Scalar array elements (e.g. chromosome `"1"`) are stored under `foreach_item_var` and as `${var.chromosome}` when `foreach_item_var = chromosome`.

Placeholders:

- `${var.name}` — scope walk
- `${var.array[0]}` — indexed array access
- `${ctx.item}` / `${ctx.index}` — shorthands for current FOREACH item/index scope vars
- `${ctx.iterationNo}` — 1-based iteration (REPEAT/FOREACH)

Deploy: [`wf_sql_foreach_support.sql`](wf_sql_foreach_support.sql) (after write-path parity).

## Instance `context_json`

Top-level keys become scope-0 variables via `wf_init_instance_scope_from_context`.

Required for the default pipeline templates:

| Key | Type | Description |
|-----|------|-------------|
| `comparisons` | array of objects | OvR comparison fan-out; each object should include paths/labels used in templates |
| `chromosomes` | array of strings | Per-chromosome fan-out |
| `projectPath` | string | Passed to worker payloads |
| `context` | string | Methylation context (or opaque domain tag) |
| `centroid1Dir` | string | Shared control centroid directory |
| `group1Label` | string | Worker group label for control |
| `orderedComparisonLabels` | array | Progression stage order |
| `workerTool*` | strings | Optional; tool name in JSON (`MethylCentroid`, …) so the same graph can target different worker adapters |

Example (PCa3): [`instance_context_examples/pca_ovr.json`](instance_context_examples/pca_ovr.json)

A **planner service** (outside SQL) can build `comparisons` / `chromosomes` from any project JSON (`project_PCa3.json`, future cohorts) without re-seeding the workflow graph.

## Deploy & run

```sql
-- once (after wf_sql_foreach_support.sql)
:r wf_data_driven_pipeline_seed.sql

EXEC wf.sp_delete_workflow_def @workflow_name = N'DataDrivenPipeline';  -- rebuild only

INSERT INTO wf.workflow_instance (workflow_version_id, status, context_json)
SELECT TOP (1) wv.id, N'CREATED', CAST(<pca_ovr.json contents> AS json)
FROM wf.workflow_version wv
JOIN wf.workflow_def wd ON wd.id = wv.workflow_def_id
WHERE wd.name = N'DataDrivenPipeline';

INSERT INTO wf.instance_cursor (workflow_instance_id) VALUES (SCOPE_IDENTITY());
EXEC wf.sp_start_workflow_instance @workflow_instance_id = SCOPE_IDENTITY();
```

Workers poll as usual; task count scales with `len(comparisons) × len(chromosomes) × 3 + 3` post steps.

## Deprecated static seeds

These **hard-coded** generators are superseded for production use:

- `wf_pca_two_group_seed.sql` (PCaTwoGroupFlow)
- `wf_pca_ovr_seed.sql` (PCaOvrFlow)

Keep them only as historical references or delete after migrating instances to **DataDrivenPipeline**.

## Related

- FOREACH implementation: [`wf_sql_foreach_support.sql`](wf_sql_foreach_support.sql)
- Design notes: [`wf_foreach_design.md`](wf_foreach_design.md) (status: implemented)
- Capability check: [`../CAPABILITY_CHECK.md`](../CAPABILITY_CHECK.md)
