# Distributed MethylValidation on shared storage {#sec-distributed-methylvalidation}
> **Status:** This chapter documents the **legacy file-queue** path (`plan-runs` / `run-task`). New cluster deployments should use **`methyl-gateway` + `methyl-worker`** with the same DomainPrograms as local `methyl-workflow-run` — see [Deployment and distributed workflow](14-deployment-and-distributed-workflow.md) and [Orchestration](04-orchestration-workflow-run.md).

## Purpose

Run discovery Monte Carlo (and related queue-backed work) on **several machines** when a single host cannot finish `n_iterations` in acceptable wall time. Every worker must read and write the **same** `output_base` tree (commonly shared NFS under `/work/...`).

## Prerequisites

- One **MonteCarloConfig** (dedicated `mc.json` or profile `actionConfig.validation` merged at plan time) with `output_base` on shared storage
- The same virtual environment and `methyl-*` CLIs on each worker
- R/W access to: `output_base/<project_name>/monte_carlo_runs/` and the `queue/` subtree created by planning

## Layout (under `monte_carlo_runs/`)

| Path | Role |
|------|------|
| `queue/mc_config.json` | Snapshotted config for workers |
| `queue/plan_runs.json` | Planned runs and task JSON paths |
| `queue/tasks/run_####.json` | One task = one `run-task` invocation |
| `queue/queue_manifest.jsonl` / `queue/commands.sh` | Optional export for a job scheduler |
| `run_####/queue_task_status.json` | Worker completion status |
| `run_####/queue_local_step_timings.json` | Merged by `aggregate-results` into `step_timings.csv` |

Full path reference: `packages/methylvalidation/docs/DISTRIBUTED_QUEUE.md`.

## Command sequence

**1) Plan** (one process; creates run dirs and task JSON, no heavy steps):

```bash
source .venv/bin/activate
methyl-validation plan-runs --config /work/experiments/my_mc_config.json --overwrite
# or:  methyl-validation plan-runs --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json --overwrite
```

`--overwrite` only replaces `queue/tasks/` and regenerates the plan; it does **not** remove existing `run_####` output directories. To delete all `run_####` trees before planning, add `--wipe-runs` (irreversible). Without `--overwrite`, completed runs (see `queue_task_status.json` + per-run task under `queue/tasks/`) are not written over; you can add iterations and only the new `run_####` work is planned. `run-task` skips pipeline work for runs already marked completed unless you pass `--force`.

**2) Export** (optional; for a central queue broker):

```bash
methyl-validation export-queue --config /work/experiments/my_mc_config.json
```

**3) Workers** (one job per task; repeat until the queue is empty):

```bash
methyl-validation run-task --task /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/monte_carlo_runs/queue/tasks/run_0001.json
```

**4) Aggregate** (after successful tasks, or to refresh from a partial set):

```bash
methyl-validation aggregate-results --config /work/experiments/my_mc_config.json
# add --stability to run the same DMP/gene stability pass as monolithic methyl-validation --stability
```

If adaptive stop settings are enabled for monolithic stability runs, remember queue workers do not coordinate global early termination on their own. Keep `stability_early_stop_enabled=false` for queue-first executions unless your scheduler supports coordinator-side cancellation logic.

**Legacy:** a single process may still run `methyl-validation --stability` without subcommands; behavior is unchanged.

## Example paths (`/work` pattern)

With `output_base` pointing at shared storage, `plan-runs` / `export-queue` resolve `monte_carlo_runs` the same way as the monolithic CLI: `output_base / project_name / monte_carlo_runs /`.

## Recovery

- Re-run a failed `run-task` for the same `--task` path.
- `queue_task_status.json` records `failed` vs `completed`.
- `aggregate-results` skips failed runs (when status files are present) and includes runs with computable per-run metrics.

## See also

- `packages/methylvalidation/docs/DISTRIBUTED_QUEUE.md` (canonical detail)
- `docs/theory/chapters/15-model-creation-and-validation.md` (section *Distributed Monte Carlo on shared storage*)
- [Optional hyperparameter search](15-optional-hyperparameter-search.md) (Chapter 15; search trials can use the same shared storage)
