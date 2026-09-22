# Optional hyperparameter search {#sec-optional-hyperparam-search-ops}
## When to use

After a baseline workflow-run (or legacy `methyl-validation` path) is understood, use a controlled **outer loop** to compare candidate settings—typically **Tier A** knobs in profile `actionConfig.validation` (e.g. `stability_dmp_freq`, `n_iterations` as budget, `stability_min_balanced_accuracy`). Tuning detector and model backends (Tier B/C) is a separate, often **nested** exercise; see the theory chapter on multi-stage search tradeoffs in `docs/theory/chapters/15-model-creation-and-validation.md` (*Optional pipeline hyperparameter search*).

!!! warning "Assay procedure first, then HPO"

    **Assay procedure packs** (`pipelineProcedure`) and hyperparameter search are complementary, not alternatives.

    | Layer | Role | In the HPO grid? |
    |-------|------|------------------|
    | `pipelineProcedure` | Fixed assay recipe (library protocol, SamplePrep/lifecycle, informME/deconv on/off, gene FeatureCuts axis, covariate paths) | **No** — choose once on the instance/context base |
    | `pipelineProfile` | SaMD rung / research profile (`samd_research`, …) | Fixed per search request |
    | HPO overlay / grid axes | Tier-A `actionConfig` knobs (mostly `validation.*`) | **Yes** |

    Operator order:

    1. Pick analyte + **`pipelineProcedure`** (see [ch.24](24-methylation-application-packs.md)).
    2. Run a baseline with that procedure + `samd_research`.
    3. Sweep Tier-A knobs with `scenario-start` / `hyperparam-grid-start` (or legacy `methyl-hyperparam-search`) on the **same** procedure base.
    4. Operator-gated promote the winning overlay (`portal.sp_promote_hyperparam_winner` deep-merges dotted trial keys such as `validation.stability_dmp_freq` into the existing study `actionConfig`; it does not replace the whole overlay); freeze / model-MC with covariates **after** stability search.

    Do **not** put `pipelineProcedure`, aligner, or library protocol in grid axes. Changing procedure is a different study setup, not a trial. Typical axes for gene-FeatureCuts procedures (`buffy_wgbs_*_gene_fc`, `cfdna_wgbs_plasma`): `validation.stability_gene_featurecuts_max_dmps` / `_max_genes`, `stability_target_balanced_accuracy` / `gene_featurecuts_target_ba`, `stability_gene_freq`, `n_iterations` (budget).

!!! note "Production paths: scenario vs grid"

    The `methyl-hyperparam-search` subprocess loop described below is the **legacy** driver.

    **Scenario (assumption check):** `methyl-study-start scenario-start` with a `HyperparamScenarioRequest` overlay — one workflow instance + `executionScopeId`, no Cartesian axes. Use this to confirm personal assumptions or evaluate a stability target (BA gates, FeatureCuts caps, frequencies). JSON `null` clears a site/profile knob. Ensure the instance context (or study start base) already carries `pipelineProcedure`.

    **Grid search:** `methyl-study-start hyperparam-grid-start` expands axes into N instances (`cfg.hyperparameter_search_run` + trials). Score with `hyperparam-grid-score`.

    Workers never run the outer loop. See [Portal remote control](../architecture/portal-remote-control.md) and [config propagation — methylation](../architecture/config-propagation-methylation.md).

## What it is

- A **read-only** scalar **objective** \(J\) is computed from artifacts under `monte_carlo_runs/`.
  Metric sources (first match wins):
  1. `metrics_summary.json` at the MC root (or a single `model_mc/*/metrics_summary.json`)
  2. Else aggregate `run_*/gene_stability/gene_featurecuts_metrics.json` into a synthetic summary
     (`balanced_accuracy` mean/median) — so **stability-only** Tier-A grids can score gene FeatureCuts BA
     without freeze/model-MC
  3. Optional `stability/stability_summary.json` for panel-size terms / min-stable constraints
- The `methyl-hyperparam-search` entry point is a small **grid** driver: for each grid point it writes `work_dir/trial_NNNN/mc_config.json` with a merged `MonteCarloConfig` and a distinct `output_base`, runs `methyl-validation` (e.g. with `--stability`), then scores the run.
- It does **not** use `sklearn.model_selection` CV: each evaluation is a subprocess and file read.
- This is **worker-package** capability on a promoted release. Experiment knobs live as JSON under `/work` (grids, weights, base MC config). It does **not** change `wf`/`cfg` schemas.

**Formula and weights:** `packages/methylvalidation/docs/HYPERPARAMETER_SEARCH.md` (full \(J\), `ObjectiveWeights`, constraint semantics).

## Environments

The search is deliberately separated across three environments. Keeping them distinct is what lets you experiment against a promoted release without editing platform code.

| Environment | Where | Role for hyperparameter search |
|-------------|-------|--------------------------------|
| **Development** | Repo checkout + `.venv` | Author and unit-test the driver (`methyl-hyperparam-search`), objective, and mapper/gene-select fixes. Run `--dry-run` and small smoke grids only. This is the source of package fixes, not the place for long experiments. |
| **Experimentation** | Promoted release worker venv (`/work/goliath/current`) + JSON under `/work` | Run the outer loop. All tunable knobs live as JSON (`base_mc_config.json`, `grid_*.json`, `weights_*.json`); the driver scores gene-FeatureCuts BA from stability-only runs. No `wf`/`cfg` schema changes. |
| **Production** | DB-backed gateway workflows | Consumes the **winning** locked configuration via `resolvedConfig`; profiles/programs come from the runtime-bundle. The search driver itself is not part of the production task path — only its result is promoted. |

Package fixes that the experimentation environment depends on (gene-FeatureCuts BA objective fallback, nullable gene-select caps, mapper discovery-CSV default) must be in the **promoted release** before you rely on them there; until then, run from a repo checkout that has them.

## Command example

Use a dedicated MC config and a work directory on shared storage if many trials. Prefer the **release** worker venv (`/work/goliath/current/env/worker.env`), not a git checkout:

```bash
set -a && source /work/goliath/current/env/worker.env && set +a
export PATH=/work/goliath/venv-aarch64/bin:$PATH
methyl-hyperparam-search \
  --config /work/experiments/my_mc_config.json \
  --work-dir /work/experiments/hp_run1 \
  --grid '{"stability_dmp_freq": [0.6, 0.7], "stability_min_balanced_accuracy": [null, 0.5]}' \
  --weights-json /work/experiments/weights_ba.json \
  -- \
  --stability
```

- Extra `methyl-validation` flags follow the search options and are forwarded verbatim. The leading `--` separator is **optional** (`... --stability` and `... -- --stability` are equivalent).
- JSON `null` in `--grid` omits that field so the base config value is kept (it does **not** mean “uncap”). To raise a site cap, put an explicit integer in the grid or base config.
- Results: `work-dir/search_summary.json` (per-trial rows and best feasible trial, if any).
- `--weights-json` can point to a file for `ObjectiveWeights` (e.g. BA-dominant: `w_balanced_accuracy=1`, `stat=mean`).
- `--baseline-summary /path/to/metrics_summary.json` enforces **rollout-style** feasibility vs a baseline (same idea as `methyl-validation --rollout-compare`); see `packages/methylvalidation/docs/ROLLOUT.md` and `packages/methylvalidation/docs/HYPERPARAMETER_SEARCH.md`.
- `--dry-run` only writes trial configs and paths; no pipeline subprocesses.

### Resuming after a failure

- `--stability --resume N --skip-centroid` reuses existing centroids and continues detector → mapper → gene-select from `run_000N`. Use this to resume trials that failed mid-pipeline (e.g. a mapper error) without recomputing centroids.
- `--skip-detection` only recomputes stability **frequency** from existing detector outputs; it does **not** re-run mapper or gene-select. It cannot recover a trial whose mapper/gene-select step failed — a full `--stability` (optionally with `--skip-centroid`) is required there.

## Pitfalls

- **Discovery vs calibration:** settings that optimize stability panel size may not optimize NLL, Brier, or ECE; prefer a **two-stage** strategy or tight constraints, not a single ad hoc weight mix.
- **`n_iterations`:** treat primarily as **compute budget** to stabilize medians, not a dimension to grid-search widely without cost awareness.

## See also

- [Methylation application packs](24-methylation-application-packs.md) — `pipelineProcedure` recipes (fixed base under HPO)
- `packages/methylvalidation/docs/HYPERPARAMETER_SEARCH.md`
- `packages/methylvalidation/docs/ROLLOUT.md` (promotion / dual-run thinking)
- `docs/theory/chapters/15-model-creation-and-validation.md` (*Optional pipeline hyperparameter search*)
- [Distributed MethylValidation on shared storage](13-distributed-methyl-validation.md) (Chapter 13; parallel execution of a single config’s iterations)
- [Buffy gene Tier-A cross-VM](../deployment/buffy_gene_tier_a_cross_vm.md) — worked gene-FeatureCuts grid on a buffy procedure base
