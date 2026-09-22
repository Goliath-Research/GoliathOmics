# Distributed queue workflow (MethylValidation)

This flow splits **discovery** Monte Carlo work into a **plan** phase (fast, metadata only on shared storage) and **worker** tasks (centroid + detector, or predictor-only) that a central queue can schedule across many machines. All workers must see the same output tree (e.g. NFS at `/work/...`).

## Storage layout (under `output_base / project_name / monte_carlo_runs /`)

| Path | Purpose |
|------|---------|
| `run_####/` | One MC iteration: `project.json`, train/val CSVs, logs, outputs |
| `queue/mc_config.json` | Snapshot of `MonteCarloConfig` for workers |
| `queue/plan_runs.json` | Machine-readable list of planned runs and `task_json` paths |
| `queue/tasks/run_####.json` | One task descriptor per run (for `run-task`) |
| `queue/queue_manifest.jsonl` | One JSON object per line: command + `expected_outputs` (from `export-queue`) |
| `queue/commands.sh` | Shell one-liner per task (no queue API) |
| `queue/queue_summary.json` | Counts and paths after export |
| `queue/claims/` | Optional file-based lease directory (reservation by workers) |
| `run_####/queue_task_status.json` | Worker completion record (from `run-task`) |
| `run_####/queue_local_step_timings.json` | Step timings for `aggregate-results` to merge into `step_timings.csv` |

## Workflow

1. **Plan** (one process, short): generate all run directories and task JSON, no heavy pipeline.
   ```bash
   methyl-validation plan-runs --config mc.json --overwrite
   # or: --project /work/.../project.json
   ```
   `plan-runs --overwrite` **refreshes** `queue/tasks/` and per-run plan inputs; it does **not** delete existing `run_####` directories (so completed worker outputs stay). To remove all `run_####` trees and start from a clean slate, use `--wipe-runs` (destructive).
   **Without** `--overwrite` (and without `--wipe-runs`), runs that already have `queue_task_status.json` with status `completed` and a matching `queue/tasks/run_####.json` are left **unchanged** on disk (same `project.json` / list files); you can raise `n_iterations` and only new `run_####` directories are materialized. `run-task` also exits 0 without re-running the pipeline if the run is already `completed` (use `run-task --force` to redo), so schedulers can re-submit a full job array safely.

2. **Export** (optional): build manifest for your queue broker.
   ```bash
   methyl-validation export-queue --config mc.json
   # or: --monte-carlo-runs /work/.../project_name/monte_carlo_runs
   ```

3. **Workers**: each job runs exactly one task (repeat until the queue is empty).
   ```bash
   methyl-validation run-task --task /work/.../monte_carlo_runs/queue/tasks/run_0001.json
   ```

4. **Aggregate** (after all tasks succeed, or to refresh metrics from a partial set):
   ```bash
   methyl-validation aggregate-results --config mc.json
  # add --stability to run the same DMP/gene stability pass as legacy --stability
  # adaptive early-stop metadata is recorded in stability_summary.json when enabled in config
   ```

5. **Legacy path unchanged**: a single process can still run the monolithic `methyl-validation` without subcommands (sequential MC loop).

When planning distributed stability runs, keep adaptive stop (`stability_early_stop_enabled`) disabled unless the scheduler contract allows coordinator-side early termination. Queue workers execute per-task descriptors and do not globally stop queued tasks on their own.

## Example: `/work` project (e.g. prostate)

If your project file lives at `/home/ubuntu/Work/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json`, with `output_base` pointing at shared storage, `plan-runs` / `export-queue` resolve `monte_carlo_runs` the same way as the legacy CLI under `output_base / project_name / monte_carlo_runs`.

## Recovery

- Re-run a failed `run-task` for the same `--task` JSON; outputs are under the run directory.
- `queue_task_status.json` records `failed` vs `completed`.
- `aggregate-results` ignores runs with failed status (when `queue_task_status.json` is present) and includes runs with computable per-run metrics.

## Central server and workers

The queue manifest is **backend-agnostic** (JSON lines). A central service can hand each worker one manifest line; workers need the same venv, `methyl-*` CLIs, and R/W access to the shared `monte_carlo_runs` tree.

## Task schema

Task files use `task_schema_version: "1.0"` and the strict Pydantic model `DiscoveryRunTaskV1` in `methyl_validation.task_schema` (`extra="forbid"`).

Optional seed fields for distributed `plan-runs` / `run-task`:

| Field | Purpose |
|-------|---------|
| `centroid_seed_root` | Root of `{monteCarloRunsRoot}/_centroid_seed` when workers must resolve seed paths |
| `centroid_seed_groups` | Typed `CentroidSeedGroup[]` copied from planner output |
| `detector_step_override_path` | Path to detector step override JSON (replaces inline `detector_step_override` dict) |

Workers copy each group's seed centroid tree into the run `centroidDir` before applying cohort-relative `addSamples` / `removeSamples` when `centroidSeedDir` is set on the task or in `centroidGroups`.

## Post-freeze enricher queue

After **`--freeze`** (mapper complete), Enrichr can run as **one task per comparison** via `methyl-enricher` (not `methyl-validation`):

```bash
methyl-enricher plan-tasks --project /work/.../monte_carlo_runs/production/project.json
methyl-enricher export-queue --project /work/.../monte_carlo_runs/production/project.json
methyl-enricher run-task --task /work/.../production/enricher/queue/tasks/enricher_<label>.json
methyl-enricher verify-complete --project /work/.../monte_carlo_runs/production/project.json
```

Artifacts live under `monte_carlo_runs/production/enricher/queue/` (manifest, `commands.sh`, per-task JSON).

When `actionConfig.enricher.distributed` is **true**, monolithic freeze runs mapper + **plan-tasks** only; an external scheduler must run `run-task` workers, then `verify-complete`, before progression.

Monolithic alternative (single host): `methyl-enricher --project .../production/project.json --ensure-complete` (used by default freeze when `ensure_complete` is true).

## Biological readiness chain

Before `--model`, run the combined gate:

```bash
methyl-validation biological-readiness /work/.../Healthy_vs_PCa1-5-CG
```

This runs `verify-complete` → `methyl-disease-progression --strict-missing` → `methyl-stability-freeze-readiness`, then set `biological_review_confirmed: true` in config.
