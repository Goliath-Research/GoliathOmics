# Pipeline-wide hyperparameter search

This document specifies the **objective** \(J(\theta)\), the **search space** (by tier), **search algorithms**, and the **implementation** in this package.

## 1. Objective \(J(\theta)\)

Let \(\theta\) denote tunable parameters (merged into `MonteCarloConfig` and/or project `actionConfig`).

**Implemented in** `methyl_validation.optimization.objective_from_monte_carlo_artifacts`:

- **Inputs (read-only), in order:**
  1. `monte_carlo_runs/metrics_summary.json` — scalar metrics with `mean` and `percentiles.p50` per metric (preferred when present).
  2. Else a single `monte_carlo_runs/model_mc/*/metrics_summary.json` if exactly one model-MC summary exists.
  3. Else **gene FeatureCuts fallback:** aggregate every
     `run_*/gene_stability/gene_featurecuts_metrics.json` via
     `aggregate_gene_featurecuts_metrics_summary` into a synthetic summary with
     `balanced_accuracy` (and related keys) `mean` / `percentiles.p50`.
     Use this for **stability-only** Tier-A search before freeze/model-MC.
  4. Optional `monte_carlo_runs/stability/stability_summary.json` —
     `dmp_stability.stable_dmps_at_threshold`, `gene_stability.stable_genes_at_threshold`.

- **Scalarization (maximize):**
  \[
  J = w_{\mathrm{BA}} \cdot \mathrm{BA}_s + w_{\mathrm{F1}} \cdot \mathrm{F1}_s
      - w_{\mathrm{NLL}} \cdot \mathrm{NLL}_s - w_{\mathrm{Brier}} \cdot \mathrm{Brier}_s - w_{\mathrm{ECE}} \cdot \mathrm{ECE}_s
      + w_{\mathrm{panel}} \cdot f(n_{\mathrm{DMP}}) + w_{\mathrm{genes}} \cdot f(n_{\mathrm{genes}})
  \]
  where subscript \(s \in \{\mathrm{mean}, \mathrm{median}\}\) is chosen via `ObjectiveWeights.stat`, and
  \(f(n) = \min(n, \mathrm{cap})/\mathrm{cap}\) caps the reward for very large panels.

- **Missing metrics:** If no metrics summary and no gene FeatureCuts per-run files exist, the trial is infeasible (`missing_metrics_summary`). If `w_nll > 0` but NLL is absent (e.g. detector-only or gene-FC-only runs), that term is skipped. `w_balanced_accuracy` and `w_macro_f1` require the corresponding metric when non-zero.

- **Constraints:** Optional `ConstraintSet` delegates to `rollout.evaluate_dual_run` (same **mean**-based guards as rollout) comparing the **candidate** `metrics_summary.json` to a **baseline** file. If the recommendation is not `promote`, \(J\) is treated as infeasible (`feasible=False`, large negative value).

- **Minimum stable panel:** If `min_stable_dmps` (or genes) is set, `stability_summary.json` must exist and counts must meet minima unless `relax_min_stable=True`.

- **Progression (multi-stage):** Not implemented in code; for disease-stage work, define a second objective over per-stage outputs (e.g. Jaccard of module sets) and combine in a separate script once single-stage search is stable.

## 2. Hyperparameter tiers (search space)

| Tier | Scope | Examples |
|------|--------|----------|
| **A** | `MonteCarloConfig` / `actionConfig.validation` | `train_fraction`, `n_iterations`, `stability_dmp_freq`, `stability_gene_freq`, `stability_min_balanced_accuracy`, adaptive stop (`stability_early_stop_enabled`, `stability_min_iterations`, `stability_convergence_*`), FeatureCuts: `stability_featurecuts_enabled`, `stability_target_balanced_accuracy`, `stability_min_core_dmps` |
| **B** | Project `actionConfig` for detector/classifier | Thresholds, k for DMPs, options exposed in JSON |
| **C** | Model backend | `model_backend`, `tabular_methods`, `generative_*` — use **nested** search after Tier A, often on a **fixed** frozen panel |

**Rule:** Full Cartesian product over A×B×C is usually too large. Use **small grids** on Tier A, **random** or **Bayesian** search on B/C, or **successive halving** with a low `n_iterations` pilot.

## 3. Search methods

| Method | Use when |
|--------|----------|
| **Grid** | 1–2 discrete dimensions, very few values (e.g. `stability_dmp_freq` × `stability_min_balanced_accuracy`) |
| **Random** | Many mixed types; cheap exploration |
| **Successive halving / Hyperband** | You can score cheap trials with `n_iterations` small, then full runs for winners |
| **Bayesian (Optuna, skopt)** | Low-dimensional \(\theta\), less noisy J (increase MC iterations) |

`sklearn.model_selection.GridSearchCV` is **not** used: the “estimator” is an out-of-process `methyl-validation` run.

## 4. Software

### 4.1 `objective_from_monte_carlo_artifacts`

```python
from pathlib import Path
from methyl_validation.optimization import (
    objective_from_monte_carlo_artifacts,
    ObjectiveWeights,
    ConstraintSet,
)
w = ObjectiveWeights(w_balanced_accuracy=1.0, w_macro_f1=1.0, stat="median")
# optional: c = ConstraintSet(baseline_metrics_summary_path=Path("baseline/metrics_summary.json"))
r = objective_from_monte_carlo_artifacts(Path(".../monte_carlo_runs"), w, None)
print(r.value, r.feasible, r.details)
```

### 4.2 `methyl-hyperparam-search` (grid driver)

After a normal pipeline layout exists under each trial’s `output_base` (isolated per grid point):

```bash
methyl-hyperparam-search \
  --config my_mc_config.json \
  --work-dir /work/experiments/hp_run1 \
  --grid '{"stability_dmp_freq": [0.65, 0.7, 0.75], "stability_min_balanced_accuracy": [null, 0.5]}' \
  -- \
  --stability
```

- Each trial writes `work-dir/trial_NNNN/mc_config.json` and sets `output_base=work-dir/trial_NNNN/out`.
- Results: `work-dir/search_summary.json` (best trial + all rows).
- `--weights-json` can point to a JSON file for `ObjectiveWeights`.
- `--baseline-summary path/to/metrics_summary.json` enables rollout constraints.
- `--dry-run` only writes configs and paths (no `methyl-validation` subprocess).
- Extra `methyl-validation` flags are parsed with `parse_known_args` and forwarded verbatim; the leading `--` separator is **optional** (`... --stability` == `... -- --stability`).

**Example 2×2** (as in the design): `stability_dmp_freq` ∈ {0.6, 0.7} × `stability_min_balanced_accuracy` ∈ {null, 0.5} — use JSON `null` to omit the field (keeps the base config value) or pass explicit numbers.

### Resuming failed trials

- `--stability --resume N --skip-centroid` reuses centroids and continues detector → mapper → gene-select from `run_000N` — use this to recover trials that failed mid-pipeline.
- `--skip-detection` only recomputes stability **frequency** from existing detections; it does **not** re-run mapper/gene-select and cannot recover a trial that failed at those steps.

## 4a. Environments (development / experimentation / production)

| Environment | Where | Role |
|-------------|-------|------|
| **Development** | Repo checkout + `.venv` | Author/unit-test the driver, objective, and mapper/gene-select fixes; `--dry-run` and smoke grids only. |
| **Experimentation** | Promoted release venv (`/work/goliath/current`) + JSON under `/work` | Run the outer loop; knobs are `base_mc_config.json` / `grid_*.json` / `weights_*.json`; scores gene-FeatureCuts BA from stability-only runs; no `wf`/`cfg` schema changes. |
| **Production** | DB-backed gateway workflows | Consumes the winning locked config via `resolvedConfig`; the search driver is not on the production task path — only its result is promoted. |

The experimentation environment depends on package capabilities (gene-FeatureCuts BA objective fallback, nullable gene-select caps, mapper discovery-CSV default) being in the promoted release; until then run from a repo checkout that has them.

## 5. Large shared-storage projects (e.g. `/work/.../project.json`)

Set `output_base` in the base config (or per-trial under `--work-dir`) to shared NFS. The driver isolates each trial under `work-dir/trial_*/out` so runs do not overwrite each other. See [DISTRIBUTED_QUEUE.md](DISTRIBUTED_QUEUE.md) for queue-based execution of the same `methyl-validation` invocations at scale.

## 6. Pitfalls

- **Discovery vs calibration:** Best stability settings may not align with best NLL/ECE; prefer a two-stage search or strong constraints on rollout metrics.
- **`n_iterations`:** Treat as **compute budget** to reduce variance of the median, not as a free hyperparameter to grid-search widely.
- **Baseline for constraints:** The baseline `metrics_summary.json` must use the same metric schema (predictor vs detector-only) as candidates. Gene-FC-only synthetic summaries are not interchangeable with model-MC summaries for rollout compare.
- **`tabular_max_dmps` / gene FeatureCuts caps:** Grid JSON `null` means “omit / keep base”, not uncap. Prefer explicit raised integers in the grid on older releases; study/MC `"max_dmps": null` clearing a site cap requires a release that honors nullable overlays in gene-select.
- **Release vs checkout:** Run `methyl-hyperparam-search` from the promoted worker venv. Grids/weights/base configs stay under `/work`. No `wf`/`cfg` schema changes are required for Tier-A grids.

## 7. References

- Metrics schema: `validator_metrics.METRICS_SCHEMA_VERSION`, `SCALAR_KEYS`
- Rollout checks: `rollout.evaluate_dual_run`
- Config model: `config.MonteCarloConfig`
