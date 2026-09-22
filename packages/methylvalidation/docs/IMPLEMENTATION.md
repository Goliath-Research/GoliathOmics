# MethylValidation Implementation

**Related documentation:** [THEORY.md](THEORY.md), [Usage (Docker and venv)](USAGE.md), [MethylPredictor USAGE](../../methylpredictor/docs/USAGE.md), and the canonical theory book at [../../../docs/theory/README.md](../../../docs/theory/README.md).

This document describes how MethylValidation is implemented: it orchestrates stratified splits, per-iteration project generation, subprocess pipeline runs, and aggregation of MethylPredictor metrics and step timings.

## Configuration Philosophy: Single Source of Truth

Project configurations should follow a **single source of truth** principle:

- **Global level**: Define `controls`, `diseases`, `samples_base_path`, etc.
- **Step-specific sections**: Only override settings that are different for that step
- **Avoid duplication**: Do not repeat global data structures in step configs

### Example of Clean Structure:

```json
{
  "project_name": "Healthy_vs_PCa1-4-CG",
  "samples_base_path": "/work/samples",
  "controls": { ... },           // Define once
  "diseases": { ... },           // Define once
  "actionConfig": {
    "centroid": { ... },
    "detection": { ... },
    "predictor": {
      "debug": false,
      "panel": { ... }           // Only step-specific overrides
    },
    "validation": { ... }        // MC-specific settings
  }
}
```

### 1. Model Creation (Monte Carlo + Stability + Freeze)

- Run with `--stability` to evaluate many random splits and aggregate stable DMPs
- Run with `--freeze` to build production biological outputs from the stable panel
- Run with `--model-mc` to perform full backend-specific MC retrain+test loops for model selection
- Run with `--select-best-model` to select best backend and build final production model artifacts
- Uses `actionConfig.validation` settings from the project

### 2. Model Use for Prediction (Predictor-only)

- Uses same splits as Model Creation but only runs predictor
- Evaluates the frozen production model

**Command examples:**
```bash
# Model Creation
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --stability
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --freeze

# Model Selection Monte Carlo
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --model-mc --model-mc-all

# Final model training (all data)
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --select-best-model --model-mc-all

# Model Use for Prediction
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --predictor-only
```

---

## Architecture overview

MethylValidation orchestrates stratified splits, project generation, and pipeline execution via subprocess calls. It supports both workflows described above.

### Package mapping by stage

- **MC loop (`methyl-validation` default, with optional `--stability`)**: per iteration runs `methyl-centroid` then `methyl-detector` (`run_pipeline_for_iteration`, `run_pipeline_for_iteration_multiclass`). When `stability_gene_featurecuts_enabled`, also runs `methyl-mapper` and in-process gene FeatureCuts (`gene_featurecuts.run_gene_featurecuts_for_iteration`).
- **`--stability`**: after the MC loop, runs in-process stability aggregation (`run_stability_analysis`) over detector discovery outputs and optional classifier gene panels.
- **`--freeze`**: runs `run_pipeline_for_production`: `methyl-centroid` -> `methyl-detector` (fixed panel) -> `methyl-mapper` -> `methyl-enricher`, then optional `methyl-disease-progression`.
- **`--model-mc`**: full retrain+test MC for model selection. With `--model-mc-all`, MethylValidation first builds a shared iteration set (`model_mc/shared/run_XXXX`) for split + centroid + detector, then runs backend-specific train/predict stages under `model_mc/<backend>/run_XXXX`. **Every** enabled backend (`ecdf`, `tabular_sklearn`, `generative_hybrid`) fits on the same shared `train_*` / project groups and scores holdout via `resolve_eval_paths_and_labels(..., evaluation_partition="test")` → `test_groups.json`, writing `train_metrics.json` / `test_metrics.json`. Aggregation is fail-closed unless `test_metrics.json` resolves (`metrics_source=model_test`) for that backend. When split source is reusable from primary MC runs, centroid/detector artifacts are linked into shared/backend run roots instead of recomputing.
- **`--model`**: runs `run_pipeline_for_model`. Backend comes from `actionConfig.validation.backend_profiles` (or validated CLI override). For `ecdf`, steps are `methyl-classifier` -> `methyl-predictor`. For `tabular_sklearn` and `generative_hybrid`, steps are in-process bundle -> train -> predict and do not re-run `methyl-detector`.
- **Aggregated ECDF observed-hybrid mode**: when `model_backend=ecdf` and `ecdf_aggregated_enabled=true`, trainer API runs `ecdf-aggregated-train` -> `ecdf-aggregated-predictor`, then optional `ecdf-second-stage` when `ecdf_second_stage_enabled` and/or `covariates_path` is set. Observed-hybrid features enter the second-stage stacker only when `ecdf_second_stage_include_observed_hybrid` is true (restricted to the freeze-time stable gene panel).
- **`--select-best-model`**: ranks backend model-MC summaries and runs final all-data production model build using selected backend.
- **`--post-model-validation`**: runs MC holdout evaluation against frozen production artifacts only (no retraining). `ecdf` dispatches predictor-only runs; `tabular_sklearn` and `generative_hybrid` dispatch frozen model inference via backend predictors.
- Legacy flat backend keys under `actionConfig.validation` are now rejected; migration is handled by `methyl-validation-migrate-backend-config`.
- **`--predictor-only`**: MC iterations that run only `methyl-predictor` using frozen artifacts.
- **`--rollout-compare`**: compares baseline/candidate `metrics_summary.json` and writes promotion/hold report using rollout thresholds in `MonteCarloConfig`.

ECDF/Bayesian remains methylation-only by design at the first stage. The optional ECDF second stage (observed-hybrid and/or covariates) and the `observed_hybrid` path used by ECDF-aggregated/tabular/generative backends share a unified mapped-feature builder with explicit family toggles:

- `feature_family_set=dmp_scored`: aggregated DMP-family observed metrics (`max_weighted_directional_score`, etc.). Legacy alias: `dmp`.
- `feature_family_set=gene_scored`: comparison-level `gene_directional_score__{comparison}` features from frozen gene panels (`frozen_genes_production.csv`) and per-comparison DMP effects (no `gene::` columns), plus gene-level analogs of dmp_scored centroid aggregates (`gene_max_weighted_directional_score`, `gene_weighted_centroid_contrast_score`, `gene_weighted_directional_agreement__{cancer}`, `gene_weighted_cosine_similarity_to_cancer_centroid__{cancer}`, `gene_weighted_cosine_distance_to_centroid__{class}`). No gene-level `weighted_healthy_tail_evidence` (histogram) yet.
- `feature_family_set=structural_scored`: comparison×region pooled features from `frozen_gene_features.csv` and per-locus mapper annotations; emits `structural_directional_score__{comparison}__{region}` plus companion columns only for supported `(comparison, region)` pairs (dynamic schema).
- `feature_family_set=dmp_scored+gene_scored`: DMP-family metrics plus gene-directional scores (recommended when using mapper gene panels without legacy per-gene columns). Legacy alias: `dmp+gene_scored`.
- `feature_family_set=dmp_scored+structural_scored`: DMP-family metrics plus structural-directional scores. Legacy alias: `dmp+structural_scored`.
- Unfiltered `gene`, `structural`, `dmp_scored+gene`, `dmp_scored+structural`, and `hybrid-all` are **rejected** (they emit one column per mapped gene / gene×region and ignore the stable panel). For promoter/intron/… focus use `structural_scored` with `region_directional_region_types`.
- combined scored families (`dmp_scored+gene_scored`, `dmp_scored+structural_scored`, `dmp_scored+chromosome`) concatenate in deterministic order.

Gene-directional score (per sample, per `comparison_label`):

- Gene panel: rows in `frozen_genes_production.csv` with `gene_support_n >= gene_scored_min_support_n` (default `2`, configurable).
- Per gene: `sum(sign(effect) * |effect| * (beta - 0.5)) / sum(|effect|)` over observed panel loci for that comparison (optional `region_weight` on loci).
- Pooled: `sum(gene_importance * sqrt(gene_support_n) * dir_g) / sum(gene_importance * sqrt(gene_support_n))` over genes with observed loci.

Additional `gene_scored` columns per comparison (same frozen panel and per-gene `dir_g` pass):

- `gene_panel_obs_fraction__{comparison}`: fraction of panel genes with at least one observed locus in the sample.
- `gene_directional_iqr__{comparison}`: IQR of per-gene `dir_g` values; left NaN when fewer than two panel genes have observed loci.
- `gene_weighted_sign_agreement__{comparison}`: weighted fraction of panel genes whose `sign(dir_g)` matches `sign(mean_effect_size)` from the frozen panel; uses the same `w_g` as directional score; NaN when no eligible genes have nonzero prior.

Gene-level dmp_scored analogs (schema `gene_scored_v6_centroid_analogs`): operate in the same per-gene directional space, using healthy/cancer/class locus reference vectors aggregated with `_per_gene_directional_value`:

- `gene_max_weighted_directional_score`
- `gene_weighted_centroid_contrast_score`
- `gene_weighted_directional_agreement__{cancer}`
- `gene_weighted_cosine_similarity_to_cancer_centroid__{cancer}`
- `gene_weighted_cosine_distance_to_centroid__{class}`

When **K ≥ 2** ordered comparisons are available, derived progression features are computed from **`gene_directional_score` only** (progression contrast schema retained under `gene_scored_v6_centroid_analogs`):

| K | Derived columns |
|---|-----------------|
| 1 | none (base 4 per comparison only) |
| 2 | `gene_directional_contrast__{first}__{last}` (= score(last) − score(first)), `gene_directional_progression_slope` |
| ≥3 | above plus `gene_directional_range` and K−1 `gene_directional_adjacent_delta__{left}__{right}` (= score(right) − score(left) for consecutive steps) |

Order resolution: `gene_scored_ordered_comparison_labels` (backend override) → `actionConfig.progression.ordered_comparison_labels` / `ordered_disease_groups` → `ProjectConfig.get_ordered_comparison_labels()` → append any remaining comparisons (sorted). Optional `gene_scored_contrast_pairs: [[left, right], ...]` adds extra contrast columns (deduped against the auto extreme pair).

NaN rules: contrast/delta NaN when either endpoint is NaN; slope requires ≥2 finite ordered scores; range requires all K scores finite.

Region-directional scores (`region_directional_score__*`) are no longer emitted under `gene_scored`. Use `feature_family_set=structural_scored` for frozen-panel-restricted region-type pooling (see below).

Structural-directional score (per sample, per `comparison_label` and `feature_type` such as `promoter`):

- Gene-feature panel: one row per `(comparison, gene, chromosome, feature_type)` in `frozen_gene_features.csv` (union hull over isoform intervals; `n_dmps_in_feature` = unique DMPs), kept when `n_dmps_in_feature >= structural_scored_min_support_n` (default `2`) and `feature_effect_compound > 0`, restricted to configured `region_directional_region_types`.
- Per `(gene, region)`: same locus formula as `gene_scored` over observed panel loci for that gene and region.
- Pooled: weighted mean of per-gene `dir_g` using `feature_effect_compound` (and optionally `sqrt(n_dmps_in_feature)`).
- Column emission: a `(comparison, region)` emits four base columns only when at least `region_directional_min_loci` panel loci intersect the classifier DMP index; otherwise the region is omitted (not exported as all-NaN placeholders).
- Default `region_directional_region_types`: `promoter`, `exon`, `intron`, `gene_body`, `terminator`.
- Mapper annotation cache (`mapper_dmp_annotations.csv`) collapses multi-feature intersections to one row per classifier locus using **priority** by default (`promoter > exon > intron > gene_body > terminator`), aligned with mapper exclusive assignment. Legacy weight-based collapse is available via `mapper_annotation_collapse_mode: weight`.
- Classifier loci with unknown/missing `feature_type` after mapper merge are assigned to `gene_body` by default (`mapper_annotation_unknown_fallback`; set `null` to disable).
- `observed_feature_report.structural_scored.partition_coverage` records classifier locus coverage across emitted region columns.

Additional `structural_scored` columns per emitted `(comparison, region)`: `structural_panel_obs_fraction__*`, `structural_directional_iqr__*`, `structural_weighted_sign_agreement__*`. When **K ≥ 2** comparisons emit base columns for a region, progression features are derived per region from `structural_directional_score__*` only (schema `structural_scored_v1_progression_contrast`).

**Operational note:** changing mapper collapse mode or region defaults requires refreezing `mapper_dmp_annotations.csv`, rebuilding the model feature bundle, deleting cached `train_dataset.h5` (or legacy `train_dataset.parquet`) if present, and retraining.

### Lean DMP feature profile (`hybrid_feature_v4_lean_dmp`)

When the DMP-scored family is active (`dmp_scored`, `dmp_scored+gene_scored`, etc.), observed-hybrid exports use a lean column set:

- **Removed from schema** (no longer computed or exported): `weighted_mean_abs_distance_margin`, `weighted_obs_fraction`, `weighted_fraction_dmps_closer_to_cancer_centroid__*`, `weighted_mean_abs_error_to_cancer_centroid__*`.
- **Quality-only** (exported in parquet/metadata, excluded from model training): `obs_fraction`, `n_obs_dmps`, `n_total_dmps` (override via `observed_feature_quality_columns`).
- **Training columns**: lean DMP aggregates (`max_weighted_directional_score`, `weighted_centroid_contrast_score`, per-comparison directional/cosine/tail features) plus any active gene/structural families.

Backends persist three name lists in model metadata and train-dataset sidecars:

- `observed_feature_names` — full export column order (parquet width).
- `training_feature_names` — columns passed to sklearn/generative/ECDF models.
- `quality_feature_names` — diagnostics only; still used at predict time for low-evidence filtering via `obs_fraction`.

For E1 `dmp_scored+gene_scored` with four comparisons, gene_scored contributes 16 base summaries + 6 progression derived + gene-level centroid analogs (`gene_max_weighted_directional_score`, contrast, per-cancer agreement/cosine-similarity, per-class cosine-distance). With one comparison (binary): 4 summaries + typically 6 centroid analogs. Re-run `--model` after upgrading (schema fingerprint bump invalidates feature caches).

For gene/structural keys, per-sample value uses signed weighted centered methylation over observed loci:

- `sum(sign(effect_size) * abs(effect_size) * (beta - 0.5)) / sum(abs(effect_size))`

Only mapped keys present in the stable DMP bundle are emitted (no synthetic all-feature expansion). All backends persist observed-feature schema/report/fill values, and prediction enforces strict parity via `verify_feature_schema` to prevent train/predict drift.

The main components are:

1. **Config & Layout**: Loads `MonteCarloConfig`, rejects blind predictors, and infers binary vs multiclass layout.
2. **Data Splitting**: `stratified_split*` functions create train/validation splits.
3. **Project Generation**: Creates per-run `project.json` files with appropriate sample paths.
4. **Pipeline Execution**: Uses `pipeline_runner.py` to run the appropriate steps.
5. **Aggregation**: Collects metrics and timings from all runs.

During stability MC runs, `stability_featurecuts_enabled` and related `stability_target_balanced_accuracy` / `stability_min_core_dmps` settings are materialized per iteration as detector step overrides so detector selection policy is explicit and auditable in each `run_XXXX`.

The `--freeze` path uses `run_pipeline_for_production()` which runs: centroid → detector(with `fixed_dmp_panel`) → mapper → enricher; when `actionConfig.progression.enabled=true`, it then runs `methyl-disease-progression`.
During freeze, model-bundle preparation materializes:

- mapper annotation cache under `production/model_bundle/mapper_dmp_annotations.csv` and wires `actionConfig.model_bundle.mapper_annotation_csv`,
- frozen gene ranking panel under `production/model_bundle/frozen_genes_production.csv` and wires `actionConfig.model_bundle.fixed_gene_panel`,
- frozen gene-feature ranges under `production/model_bundle/frozen_gene_features.csv` (one row per comparison×gene×region with union hull and unique DMP support) and wires `actionConfig.model_bundle.fixed_gene_features`.

Cache generation also supports configurable per-gene mapper attributes via `actionConfig.model_bundle.mapper_gene_columns` (or `actionConfig.mapper.mapper_gene_columns` fallback), defaulting to `["gene_importance", "gene_effect_abs_wsum", "gene_support_n", "gene_score", "mean_effect_size", "gene_effect_compound", "gene_feature_effect_compound"]`; set `[]` to disable carrying extra per-gene columns.
For observed-hybrid gene families, `gene_feature_loading` determines whether training/prediction uses only frozen DMP loci (`frozen`) or expands to all loci observed inside the frozen gene-feature ranges (`range`). With `range`, each gene×region range is a genomic hull, so discontinuous exon/intron annotations can admit gap loci.

```mermaid
flowchart LR
  Config[MonteCarloConfig]
  Layout[infer_monte_carlo_layout]
  Split[stratified_split / stratified_split_multiclass]
  GenB[generate_run_project]
  GenM[generate_run_project_multiclass]
  RunB[run_pipeline_for_iteration]
  RunM[run_pipeline_for_iteration_multiclass]
  Freeze[freeze_production_model]
  ProdRun[run_pipeline_for_production]
  Centroid[methyl-centroid]
  Detector[methyl-detector\n(fixed_dmp_panel)]
  Classifier[methyl-classifier]
  Predictor[methyl-predictor]
  Metrics[validation_metrics.json]
  Aggregate[validator_metrics]

  Config --> Layout
  Layout --> Split
  Split --> GenB
  Split --> GenM
  GenB --> RunB
  GenM --> RunM
  RunB --> Centroid
  RunM --> Centroid
  Centroid --> Detector
  Detector --> Classifier
  Classifier --> Predictor
  Predictor --> Metrics
  Metrics --> Aggregate
  Config --> Freeze
  Freeze --> ProdRun
  ProdRun --> Centroid
  Centroid --> Detector
```

## MethylUtils usage

MethylValidation uses **MethylUtils** only for project loading:

- **load_project(base_project)** — To read `project_name` and project structure so that run directories are created under `output_base/project_name/monte_carlo_runs/run_0001`, etc.
- **load_project(project_path)** — Per run (binary), to resolve the predictor output directory from comparisons (`run_dir/predictors/<control>/<disease>`). Multiclass flat runs use `run_dir/predictors` directly.

Sample path resolution is done locally in MethylValidation ([split.py](../methyl_validation/split.py): `load_and_resolve_sample_paths`). Iteration metrics are produced by **MethylDetector** (balanced accuracy in `result*.json`) during the default MC loop, by backend predictors during `--model-mc`, or by frozen predictor paths during `--post-model-validation` / `--predictor-only`; MethylValidation aggregates via `iteration_scalar_metrics_from_run_dir`.

## Modules

| Module | Role |
|--------|------|
| **config.py** | `MonteCarloConfig` — required `cohorts`, plus train_fraction, n_iterations, seed, base_project, output_base, path_remap, abort_on_step_failure. Backend knobs live under `backend_profiles` only. |
| **predictor_policy.py** | `assert_monte_carlo_predictor_allowed` — reject `predictor.blind` / `test_blind_paths` for MC. |
| **split.py** | `load_and_resolve_sample_paths`; `stratified_split` (binary); `stratified_split_multiclass` (per-label train/val). |
| **project_gen.py** | `infer_monte_carlo_layout` (rejects 2 cohorts when project resolves to >2 leaves); `generate_run_project` emits canonical binary `train_*` / `test_*` CSVs and `test_groups.json`; multiclass generators emit `training_<label>.csv` / `test_<label>.csv`. Older `val_*` / `testing_*` files remain readable for split reuse. Backend evaluation receives the test manifest explicitly; it must not infer evaluation samples from the training-only project groups. |
| **pipeline_runner.py** | `run_pipeline_for_iteration` / `run_pipeline_for_iteration_multiclass`: centroid + detector only. In binary MC iterations, centroid executes as two tracked runs (`group1`, `group2`) with separate logs/timing rows and per-row `n_processed_samples` read from centroid metadata (`samples_used`). `run_pipeline_for_production`: freeze (centroid→detector→mapper→enricher) and optional `methyl-disease-progression` from `actionConfig.progression`. `run_pipeline_for_model`: classifier→predictor or backend bundle/train/predict. `run_predictor_only_*`: predictor-only. `run_post_model_validation_*`: frozen-artifact evaluation for post-model MC mode across all backends. |
| **trainer_api.py** | Backend step abstraction for `--model`. Builds backend-specific step lists (ECDF, aggregated ECDF observed-hybrid, tabular, generative) so orchestration can be extracted into a future `methylmodeltrainer` package without changing workflow CLI semantics. |
| **validator_metrics.py** | `iteration_scalar_metrics_from_run_dir`: model-MC `test_metrics.json` first, legacy predictor `validation_metrics.json` second, else mean detector `balanced_accuracy` from `detections/**/result*.json`. Model-MC ranking accepts test metrics only. |

Config-contract audit and redundancy classification are tracked in [../../../docs/reference/config-parameter-matrix.md](../../../docs/reference/config-parameter-matrix.md). Use canonical keys (`predictor`, `input_file`, `output_dir`, `ecdf_grid_size`) in new project files; legacy aliases are compatibility-only.

## Data flow (CLI)

1. Load config; `assert_monte_carlo_predictor_allowed`; `infer_monte_carlo_layout(base_project, len(cohorts))`.
2. Resolve `project_name`; create `output_base/project_name/monte_carlo_runs/`.
3. Resolve all cohort sample paths from CSVs.
4. For each default/stability iteration:
   - **Binary:** `stratified_split` → `generate_run_project` → `run_pipeline_for_iteration` (centroid group1/group2 overrides).
   - **Multiclass:** `stratified_split_multiclass` → `generate_run_project_multiclass` → `run_pipeline_for_iteration_multiclass` (centroid + detector only).
   - Read metrics via `iteration_scalar_metrics_from_run_dir(run_dir)` (predictor JSON if present, else detector `result*.json`).
   - If `stability_early_stop_enabled=true` and stability mode is active, evaluate convergence at each checkpoint (`S_k` vs `S_(k-window)` by Jaccard + size delta) and stop when thresholds pass for `stability_convergence_patience` consecutive checkpoints.
5. Aggregate → `all_metrics.csv`, `metrics_summary.json`, `step_timings.csv`, optional `resource_summary.json`.
6. For `--model-mc`, run shared split+centroid+detector preparation once (for `--model-mc-all`), then run backend model stages under isolated backend roots and emit cross-backend ranking files (`backend_ranking.csv`, `backend_ranking.json`). Reused split runs can link primary centroid/detector artifacts into shared/backend roots.
7. For `--rollout-compare`, load baseline/candidate summaries, apply configured rollout thresholds, and emit `rollout_decision.json` (or `--rollout-report` path).

**MethylPredictor:** flat-group projects with **multiclass-classifier.pkl** resolve via `resolve_predictor_config` (shared `_build_multiclass_predictor_config`). The CLI applies `--test-groups` in both single-config and per-comparison multiclass runs (`_apply_test_groups_json_to_config`).

**After a frozen production build:** `--model-mc --model-mc-all` builds shared iteration artifacts under `monte_carlo_runs/model_mc/shared/` and evaluates each backend under `monte_carlo_runs/model_mc/<backend>/`. `--select-best-model` reads backend summaries, selects best backend by configured metric/statistic, and runs final all-data production model build. `--post-model-validation` remains a descriptive frozen-model MC path under `monte_carlo_runs/post_model_validation/`.

**Disease progression synthesis:** when `actionConfig.progression.enabled` is set, freeze invokes `methyl-disease-progression --project <production/project.json>` after enricher. The progression tool reads comparison outputs (`mapper/<control>/<disease>/all-gene_name-combined.csv`, `enricher/<control>/<disease>/enrichment_merged.csv`, optional `modules_ranked.csv`) and writes long tables + summary under `<project_root>/progression`.

## Output files

| File | Description |
|------|-------------|
| **all_metrics.csv** | One row per successful iteration: iteration, run_id, run_dir, accuracy, balanced_accuracy, macro_f1, macro_recall, macro_specificity; binary runs also include sensitivity/specificity; multiclass runs include screening_sensitivity/screening_specificity when pooled control-vs-disease view is computed. |
| **metrics_summary.json** | Per-metric empirical distribution: mean, std, min, max, count, percentiles (p5, p25, p50, p75, p95). |
| **step_timings.csv** | Per step per run: `step_name`, `duration_seconds`, `return_code`, `run_id`, `run_dir`, `n_train_samples`, `n_val_samples`, and optional `n_processed_samples` (centroid rows). Binary centroid runs emit `methyl-centroid-group1` and `methyl-centroid-group2` rows. |
| **resource_summary.json** | (Optional) Mean/std duration per step, mean total time per iteration, min/max/mean `n_train_samples`, `n_val_samples`, and `n_processed_samples` when present. |
| **model_mc/shared/run_XXXX/** | Shared per-iteration project + centroid/detector artifacts reused across backends when running `--model-mc --model-mc-all`. |
| **model_mc/shared/run_XXXX/test_*.csv**, **test_groups.json** | Canonical held-out test partition. |
| **model_mc/shared/run_XXXX/centroids**, **model_mc/shared/run_XXXX/detections** | When the primary detection contract matches production, both are symlinked. After discovery-only stability + freeze, centroids are still reused and detections are rebuilt under `fixed_dmp_panel`. |
| **model_mc/\<backend\>/run_XXXX/predictors/** | `train_predictions.csv` / `train_metrics.json` are training diagnostics; `test_predictions.csv` / `test_metrics.json` are held-out results used for model selection. Legacy names alias the test artifacts. |
| **model_bundle/** | Flat HDF5 float32 design matrices for research: `train_dataset.h5`, `test_dataset.h5` (when a test split exists), and `dataset_manifest.json`. Identity columns are `sample_id`, `class_index`, `class_label`; remaining columns are backend features (ECDF second-stage: binary `prob_class1` or multiclass ALR + Neu-referenced cell ALR / binary `p_*`; tabular/generative: observed-hybrid/DMP + covariates). Tabular train-time test export and all backend scoring resolve holdout via `evaluation_partition="test"` → `test_groups.json` (never legacy predictor `test_*` paths). Re-run Model-MC with `--force-rerun` so legacy Parquet caches are replaced by `.h5` + binary-\(p\) features. |
| **model_mc/<backend>/all_metrics.csv** | Per-backend model-stage MC metrics table produced from shared runs (or standalone backend runs when `--model-mc-all` is not used). |
| **model_mc/<backend>/metrics_summary.json** | Per-backend summary statistics used for backend ranking. |
| **model_mc/backend_ranking.csv** | Cross-backend ranking by configured metric/statistic. |
| **production/selected_backend.json** | Selected backend and ranking metadata for final all-data training decision. |
| **production/model_bundle/mapper_dmp_annotations.csv** | Mapper-derived annotation cache used by observed-hybrid mapped-family feature builders. |
| **production/classifiers/ecdf_aggregated_ovr.pkl**, **ecdf_aggregated_ovr.meta.json** | Aggregated ECDF OvR model artifacts for observed-hybrid ECDF backend mode. |
| **post_model_validation/metrics_distributions_plotly.html** | Plotly dashboard with KDE (density) and ECDF (cumulative) panels for each numeric metric. |
| **predictors/feature_family_ablation.json** | Ablation-oriented scaffold emitted for observed-hybrid paths (active families + recommended matrix for BA comparisons). |
| **classifiers/tabular_method_metrics.csv** | Per-method evaluation metrics for tabular multi-method sequence runs (ordered method index, selection metric score, rank). |
| **classifiers/tabular_method_ranking.json** | JSON ranking payload for tabular multi-method selection, including method params and selection rationale fields. |
| **stability/dmp_frequency_by_chromosome.html** | Combined Plotly stability chart with per-chromosome traces for both candidate (`all`) and final (`selected`) DMPs. X=frequency (% of runs), Y=DMP count. |
| **stability/dmp_frequency_chr_<chrom>.html** | Per-chromosome Plotly charts with `all` vs `selected` count curves over frequency (%). |
| **stability/stability_summary.json** | Stability summary now includes `detector_parameters` extracted from `detections/**/results-*.json`: per-run records plus aggregated numeric/categorical distributions for minimal detector/filter fields (`n_dmps_exported`, `total_statistical_dmps`, `total_biological_dmps`, `effect_size_coverage`, `delta_mean_reduction`, `classifier_dmp_selection`, `dynamic_dmp_cutoff_enabled`). When adaptive stop is enabled, `early_stopping` diagnostics are also recorded (`enabled`, `triggered`, stop iteration, and checkpoint history with Jaccard/size-delta stats). |

For setup (Docker, venv), config reference, and how to use these outputs for metric distributions and processing/storage estimation, see [USAGE.md](USAGE.md).
