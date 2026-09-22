> **DEPRECATED.** Superseded by DomainProgram deploy (`scripts/deploy_workflow_definitions.sh`). Legacy seed: [`deprecated/wf_pca_two_group_seed.sql`](deprecated/wf_pca_two_group_seed.sql). See [`docs/reference/domain-program-language.md`](../../docs/reference/domain-program-language.md).

Yes — that understanding is correct (for the legacy SQL-seed model).

## What each piece does

| Artifact | Role in production |
|----------|-------------------|
| **`wf_pca_two_group_seed.sql`** | Defines the workflow: nodes, edges, actions, input templates. Deploy once. |
| **`wf_sql_scope_writepath_parity.sql`** (+ other parity scripts) | Engine runtime: activation, scope, output bindings, task handoff. |
| **`wf_pca_two_group_run_example.sql`** | **Not part of production.** Simulates workers in SQL for validation only. |

## How it actually runs

Once the seed and engine scripts are deployed:

1. **Create an instance** — `INSERT INTO wf.workflow_instance (... context_json ...)` with paths, groups, etc.
2. **Start it** — `EXEC wf.sp_start_workflow_instance @workflow_instance_id = ...`
3. **Remote workers poll** — `wf.sp_worker_request_task` returns READY tasks (centroid/detector) with resolved `input_json`
4. **Workers execute** — real `methyl-centroid` / `methyl-detector` processes on compute nodes
5. **Workers submit** — `wf.sp_worker_submit_result` with `result_code` + `output_json`
6. **Engine advances** — SQL procs activate the next nodes (e.g. both centroids done → `detect_{chr}` becomes READY)

The run example replaces step 3–5 with a T-SQL loop that fakes worker responses so you can assert ordering and completion without real workers.

## Summary

The workflow is fully driven by the **seed + engine procs + real worker clients**. `wf_pca_two_group_run_example.sql` is an integration test / demo script, not a runtime dependency. You can delete or skip it in production; you cannot skip the seed or the parity/runtime SQL.