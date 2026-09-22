# The Two Workflows {#sec-two-workflows}
## Overview

MethylPipeline supports two primary end-to-end operational workflows. They are designed to be run sequentially: Workflow 1 produces the final production model; Workflow 2 evaluates that model on random holdouts.

| | Workflow 1 | Workflow 2 |
|---|---|---|
| **Goal** | Build a stable, production-ready model | Evaluate the frozen production model |
| **Flag** | `--stability`, then `--freeze`, then `--model` | `--predictor-only` |
| **Steps run per iteration** | centroid → detector | predictor only |
| **Steps run once (freeze/model)** | freeze/analysis: centroid → detector(fixed) → mapper → enricher (optional progression); model: classifier → predictor | — |
| **Output** | stable panel, production biological outputs, final classifier/predictor outputs | `all_metrics.csv`, `metrics_summary.json` |
| **Answers** | Which positions and model best separate the classes on all data? | What performance distribution does the frozen model achieve? |

Both workflows are controlled by the study manifest (cohorts, paths, comparisons) plus the **pipeline profile** `actionConfig.validation` block (see [§ project configuration](11-project-configuration.md#sec-project-configuration)). Workers receive merged settings as `resolvedConfig`; MC runs snapshot them to `monte_carlo_runs/queue/mc_config.json`.

### DMP and gene modeling modes

Profile `actionConfig.validation` exposes **`dmp_modeling_mode`** and **`gene_modeling_mode`** (`discovery_only`, `featurecuts`, or `raw_pool`). These control which DMP CSV glob downstream steps consume:

| Mode | Mapper / stability input | Typical use |
|------|--------------------------|-------------|
| `discovery_only` | `dmps-*-discovery.csv` | Broad biology, mapper/enricher exploration |
| `featurecuts` (default in MC profiles) | `dmps-*-selected.csv` | BA-gated prediction panel + gene FeatureCuts |
| `raw_pool` | Full detector export pool | Legacy / experimental paths |

Gene FeatureCuts and stability aggregation follow the same mode contract. See [§ project configuration](11-project-configuration.md#sec-project-configuration) for profile examples (`dmp_modeling_mode`, `gene_modeling_mode`, FeatureCuts target BA).

---

## Workflow 1: Model Creation {#sec-workflow-model-creation}
### Theoretical Motivation

A naive approach to building a classifier would be to detect DMPs on all available samples, then evaluate the same classifier on held-out subsets. This conflates feature selection with performance estimation: the DMP panel was implicitly chosen to fit all samples, making any leave-out evaluation optimistic.

MethylValidation addresses this by repeating only the discovery portion of the pipeline on random train/validation splits. Each iteration rebuilds centroids and re-runs detection on the training cohort only. The resulting detector-side balanced accuracy summaries are used as internal estimates of discovery robustness and to filter stability runs when requested.

After many such iterations, a DMP that appears consistently in the discovery sets of most runs is more likely to reflect a true biological signal than one that appears in only a few. This motivates the stability analysis: the stable panel is a consensus over repeated random partitions of the data.

The production freeze then trains a final classifier on **all available data** restricted to the stable panel. Because the panel itself was selected using the full sample set (via the MC iterations), this is still an internal analysis; it is not a substitute for external validation on an independent cohort.

### The Three Stages of Workflow 1

Workflow 1 is easiest to reason about as three explicit stages with different statistical roles.

#### Stage A — Stability (`--stability`)

**Theoretical role.** This stage estimates *feature-selection stability* rather than final predictive performance. Let $\mathcal{D}_{\text{disc},r}$ be the discovery DMP set from split $r$. A locus-level recurrence estimator

$$
\hat{f}(d) = \frac{1}{R^*} \sum_{r=1}^{R^*} \mathbf{1}[d \in \mathcal{D}_{\text{disc},r}]
$$

acts as a Monte Carlo approximation to selection probability under re-sampling. Thresholding with $\hat{f}(d)\ge\tau$ (where $\tau=$ `stability_dmp_freq`) enforces reproducibility pressure on the panel.

**Practical role.** This stage answers: "Which loci survive repeated train/validation perturbations?" In practice:

- it runs only `centroid -> detector` per split (fast enough for many iterations),
- it creates `all_metrics.csv`/`metrics_summary.json` for detector-side internal diagnostics,
- it writes `stability/stable_dmps_production.csv`, which is the only accepted panel input for `--freeze`,
- and it allows optional run-quality filtering via `stability_min_balanced_accuracy` before recurrence counting.

For most projects, this is the stage where panel size and robustness are tuned (`n_iterations`, `train_fraction`, `stability_dmp_freq`).

When the iteration count is high and a single host is too slow, the same stability stage can be **executed in parallel** across multiple machines, as long as all workers share one output tree; see [§ distributed mc shared storage](15-model-creation-and-validation.md#sec-distributed-mc-shared-storage) (and the user manual chapter on distributed MethylValidation).

#### Stage B — Freeze/Analysis (`--freeze`)

**Theoretical role.** This stage converts the consensus panel into a deterministic production artifact. The key operation is a *panel-constrained rerun on all samples*: detector inference is no longer discovering a new panel; it is applying fixed loci and producing full downstream biological interpretation from a stable feature space.

**Practical role.** This stage answers: "Given a locked panel, what is the complete biological analysis on the full cohort?" In practice it:

1. injects `fixed_dmp_panel` into `production/project.json`,
2. rebuilds centroids on all data,
3. runs detector in fixed-panel mode,
4. runs mapper and enricher to produce gene/pathway outputs,
5. optionally runs progression synthesis for ordered disease-stage summaries.

The freeze artifacts (`production/project.json`, fixed-panel detection outputs, mapper/enricher summaries) become the canonical handoff package for model release and review.

#### Stage C — Biological Gate + Modeling (`biological-readiness`, then `--model`)

**Theoretical role.** This stage first verifies biological/operational readiness from freeze artifacts, then fits the final prediction function conditioned on the frozen panel and frozen production project. It separates *feature agreement* (handled in Stage A/B) from *final classifier parameter estimation* (handled here).

**Practical role.** This stage answers: "Are we ready to promote the frozen artifacts to final model training, and which trained artifacts should be deployed?" In practice:

1. Run `methyl-validation biological-readiness <project_root>` to chain:
   - `methyl-enricher verify-complete`
   - `methyl-disease-progression --strict-missing`
   - `methyl-stability-freeze-readiness`
2. If readiness passes, run `--model` using profile `actionConfig.validation.model_backend`:

- `ecdf` (default): runs `methyl-classifier` then `methyl-predictor`,
- `tabular_sklearn`: runs model bundle -> tabular training -> tabular predictor evaluation,
- `generative_hybrid`: runs model bundle -> latent-density generative training -> generative predictor evaluation.

For all backends, the optional biological gate (`require_biological_review_for_model`) is enforced before model creation.

`biological-readiness` runs deterministic checks regardless of Grok/xAI availability. Advisory Grok commentary is **off by default** in the chained command and enabled with `--grok-review`.

The resulting files under `production/classifiers/` and `production/predictors/` are the deployable model artifacts. Any rerun with changed panel or freeze inputs should be treated as a new model version.

### Post-Freeze Covariate Stage {#sec-post-freeze-covariates}
Between the freeze mapper work (Stage B) and final model selection (Stage C), the study lifecycle runs a covariate stage that enriches each sample with biology the ECDF path does not see on its own. These actions are cohort-level and deterministic; they do not change the stable panel.

```mermaid
flowchart TB
  subgraph prep [SamplePrep per sample]
    AQC[alignment_qc]
    FRAG[sample.fragmentomics]
    EXT[methyl_extract plus patterns.h5]
  end
  subgraph cov [Post_freeze covariates cohort]
    DM[derived_measures]
    CD["cell_deconvolution houseman or hitimed"]
    IM[info_measures]
  end
  subgraph bio [Biology and modeling]
    EN[enricher CIS-BP modes]
    SEL[select_best_model]
  end
  prep --> cov --> bio
```

`pipeline.cell_deconvolution` in this stage carries the method switch and analyte-driven trees documented in [§ methyldeconv](07a-methyldeconv.md#sec-methyldeconv). The remaining covariates (`derived_measures`, `info_measures`) and the biology step (`enricher`) are described in the [end-to-end workflow](../../architecture/end-to-end-workflow.md) and [§ methylenricher](08-methylenricher.md#sec-methylenricher).

### Pre-MC covariates (residual procedure only) {#sec-pre-mc-covariates}

The default study lifecycle above is **unchanged**. The opt-in procedure `buffy_wgbs_mvalue_residual_gene_fc` points at forked programs (`mc_stability_residual`, `study_validation_lifecycle_residual`) that compute Houseman Ω and methylation confounder scores **once before** Monte Carlo, then fit train-only M-value residualization inside each iteration and again at freeze. That is the opposite of post-freeze ALR stacking: residualization rewrites the betas that DMP calling sees; the ECDF second-stage still runs after freeze on the residual lifecycle.

Do not gate residualize nodes onto `study_validation_lifecycle.program.json`. Isolation is a separate program JSON + procedure id. Implementation: [M-value residualization](../../implementation/mvalue-residualization.md). Research note: [Buffy M-value residualization](../../research/buffy-mvalue-residualization.md).

### Per-Analyte Action Inventory {#sec-analyte-actions}
The set of actions that run — and how they are parameterized — is gated by the study `regulatory.primary_analyte`. Analyte defaults are merged into profile/site `actionConfig` by `merge_step_config` in `packages/methylutils/methyl_utils/analyte_profiles.py` (explicit profile/site keys always win). The three covered analytes:

| Action | `cfdna` | `buffy_coat` | `tissue` |
|--------|---------|--------------|----------|
| `alignment_qc` | cfDNA fragmentomics + alignment guardrails + bisulfite QC | alignment guardrails + bisulfite QC | bisulfite QC defaults (no analyte pack) |
| `sample.fragmentomics` | enabled (WPS + end motifs) | disabled | disabled |
| `methyl_extract` | `*.h5` + `*.patterns.h5` | `*.h5` + `*.patterns.h5` | `*.h5` + `*.patterns.h5` |
| `derived_measures` | chromosome / genome surrogates | same | same |
| `cell_deconvolution` | Houseman 6 Ω, or HiTIMED plasma tree (`tumor_fraction` + immune) | Houseman 6 Ω, or HiTIMED immune subtree (no tumor) | HiTIMED full tumor/immune/stromal tree; flat Houseman remains blood-oriented (mismatch — see note) |
| `info_measures` | when `*.patterns.h5` exist; skipped cleanly otherwise | same | same |
| `enricher` | `cancer-core` + CIS-BP multi-mode (gene_sets + motif_scan + annotate) | CIS-BP gene_sets only | defaults unless profile overrides |
| `validation` | `enforce_training_analyte_match: true` | `false` | not set by analyte pack |

Two caveats today: (1) there is no dedicated `tissue` entry in `analyte_profiles.py`, so tissue prep/enricher behave like the `combined`/unknown default unless a profile overrides them; the tissue-specific behavior above is the HiTIMED tree, which is selected by the `cell_deconvolution` `analyte` field rather than the analyte profile merge. (2) Flat Houseman always projects against the blood IDOL basis, so for `tissue` the meaningful compositional estimate comes from `method = hitimed` with the full tree. See [§ methyldeconv](07a-methyldeconv.md#sec-methyldeconv) for the deconvolution math and [`docs/ANALYTE_PROFILES.md`](../../ANALYTE_PROFILES.md) for the operator view.

### Steps

**Step 1: Prepare the profile and study manifest**

Ensure the selected profile defines `actionConfig.validation` with at least `train_fraction`, `n_iterations`, `seed`, and `run_stability: true`. The study manifest supplies cohorts and paths only:

```json
"actionConfig": {
  "validation": {
    "train_fraction": 0.8,
    "n_iterations": 50,
    "seed": 42,
    "run_stability": true,
    "stability_dmp_freq": 0.7,
    "stability_min_balanced_accuracy": null,
    "abort_on_step_failure": false
  }
}
```

Pass the profile via workflow context (`pipelineProfile`) or `METHYL_PROFILE`.

**Step 2: Run Monte Carlo with stability**

```bash
source .venv/bin/activate
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --stability
```

This runs `n_iterations` stratified train/validation splits. Per iteration:

1. `methyl-centroid --group group1` and `--group group2` build separate centroid H5 files for the training split.
2. `methyl-detector --project` detects DMPs between the training centroids.

After all iterations, stability analysis counts DMP recurrence across the discovery sets:

$$
\hat{f}(d) = \frac{1}{R^*} \sum_{r=1}^{R^*} \mathbf{1}[d \in \mathcal{D}_{\text{disc},r}]
$$

and writes `monte_carlo_runs/stability/stable_dmps_production.csv`.

**Step 3: Run the production freeze**

```bash
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --freeze
```

This calls `freeze_production_model()` and then (optionally) progression synthesis which:

1. Merges stable DMP CSVs into `monte_carlo_runs/production/stable_dmps_genomewide.csv`.
2. Creates `monte_carlo_runs/production/project.json` with `fixed_dmp_panel` injected into resolved `actionConfig.detection`.
3. Runs production freeze on **all samples**: centroid → detector(fixed panel) → mapper → enricher.
4. If profile `actionConfig.progression.enabled=true`, runs `methyl-disease-progression` to aggregate cross-stage mapper/enricher outputs.
5. Writes `monte_carlo_runs/production/production_summary.json`.

**Step 4: Run biological readiness gate**

```bash
methyl-validation biological-readiness /path/to/<project_root> --grok-review
```

Use `--grok-review` only when you want advisory LLM commentary in addition to deterministic verdicting.

**Step 5: Run final model build**

```bash
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --model
```

This runs `run_pipeline_for_model()`:

1. `methyl-classifier --project production/project.json`
2. `methyl-predictor --project production/project.json`

If profile `actionConfig.validation.require_biological_review_for_model=true`, `--model` is blocked until `biological_review_confirmed=true`.

### Workflow 1 Diagram

```mermaid
flowchart TD
  cfg["project.json"]
  stab["--stability: R full-pipeline iterations"]
  iter["per iteration: centroid → detector"]
  metrics["detector metrics per iteration"]
  rec["stability recurrence f̂(d)"]
  stable["stable_dmps_production.csv"]
  freeze["--freeze"]
  merge["merge → stable_dmps_genomewide.csv"]
  prod["production/project.json with fixed_dmp_panel"]
  all["all-sample freeze: centroid → detector(fixed) → mapper → enricher"]
  prog["optional disease progression"]
  ready["biological-readiness"]
  model["--model: classifier → predictor"]

  cfg --> stab --> iter --> metrics --> rec --> stable --> freeze
  freeze --> merge --> prod --> all --> prog --> ready --> model
```

*Workflow 1 — stability, freeze, and model creation (same Mermaid source for HTML and PDF).*

### Expected Outputs

After Workflow 1 completes:

```
monte_carlo_runs/
├── run_0001/ … run_000N/          ← per-iteration outputs
│   └── detections/**/result*.json
├── all_metrics.csv                ← one row per iteration
├── metrics_summary.json           ← mean, std, percentiles per metric
├── stability/
│   ├── stable_dmps_production.csv
│   └── stability_summary.json
└── production/
    ├── project.json               ← frozen project with fixed_dmp_panel
    ├── centroids/                 ← built on all samples
    ├── detections/                ← using fixed DMP panel
    ├── mapper/
    ├── enricher/
    ├── progression/                    ← optional (when enabled)
    ├── classifiers/                    ← created by --model
    └── predictors/                     ← created by --model
```

---

## Workflow 2: Model Use for Prediction {#sec-workflow-prediction}
### Theoretical Motivation

Once a production model exists, a natural question is: what balanced accuracy does it achieve on random holdouts drawn from the available cohort? This is not the same as the BA measured in Workflow 1, because Workflow 1 re-trained the model on each split while Workflow 2 applies the frozen model.

Workflow 2 provides the empirical distribution of the frozen model's performance under random sampling. It is faster than Workflow 1 (only the predictor runs per iteration), and it answers the question: *is the production model's accuracy stable across different random splits, or does it depend heavily on which specific samples are held out?*

**Important caveat.** The available cohort used in Workflow 2 overlaps with the training data for the production model (the production model was trained on all samples). This means Workflow 2 is not a hold-out evaluation in the classical sense; the test samples may also be in the training centroid. A truly external validation cohort would be needed to measure generalization to unseen data.

### Steps

**Step 1: Confirm the production model exists**

The file `monte_carlo_runs/production/project.json` must exist (created by Workflow 1's `--freeze` step).

**Step 2: Run predictor-only evaluation**

```bash
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --predictor-only
```

Per iteration, this:

1. Creates a new split-specific `project.json` with train/validation CSVs.
2. Merges the production model paths from `production/project.json` into the run-specific project (via `apply_frozen_pipeline_artifacts_to_run_project()`).
3. Runs only `methyl-predictor` on the validation split.
4. Writes `validation_metrics.json`.

After all iterations, the same aggregation as Workflow 1 produces `all_metrics.csv` and `metrics_summary.json`.

### Workflow 2 Diagram

```mermaid
flowchart TD
  prod["production/project.json frozen model"]
  cfg["project.json cohorts + validation settings"]
  loop["--predictor-only: R iterations"]
  step["split → merge frozen paths → predictor"]
  metrics["validation_metrics.json per iteration"]
  agg["all_metrics.csv + metrics_summary.json"]

  prod --> loop
  cfg --> loop
  loop --> step --> metrics --> agg
```

*Workflow 2 — frozen-model predictor-only evaluation (same Mermaid source for HTML and PDF).*

---

## Workflow 3: True Held-Out Batch Evaluation {#sec-workflow-holdout}
### Theoretical Motivation

Workflow 2 answers "how stable is the frozen model across random holdouts drawn from the available cohort?", but its holdouts overlap the training centroid, so it is not a hold-out evaluation in the classical sense ([§ workflow prediction](#sec-workflow-prediction)). Workflow 3 closes that gap: it evaluates the frozen production model on a **designated batch of samples that were never used for training or model selection**, and characterizes the *sampling distribution* of the quality metrics by bootstrap.

Each quality indicator is reported as a bootstrap distribution (mean, interval, percentiles) rather than a single point estimate.

### Designating the Held-Out Batch

Held-out samples are declared through the `validation_partitions` contract in profile/project `actionConfig.validation` (the same object used for lifecycle governance). The default hold-out role is `locked_test`; `holdout_partition` selects a different role (for example `pivotal_validation` or `post_market_monitoring`).

The hold-out is **stratified per class**: holding out 10% removes 10% of *each* group independently, so class balance is preserved in both the hold-out and the remaining active pool. The helper `methyl_validation.holdout_eval.stratified_holdout_by_fraction(cohorts, fraction, seed=...)` produces such a split (returning `{label: {"holdout": [...], "active": [...]}}`); its `holdout` lists populate the partition. The **active** remainder (e.g. 90%) is what `--stability` consumes, and stability then further partitions the active pool into its own train/validation splits (for example 80/20 per iteration). The hold-out samples never enter stability or the final freeze.

```json
"actionConfig": {
  "validation": {
    "validation_partitions": {
      "locked_test": ["batch7/S001", "batch7/S002", "batch7/S003"]
    },
    "holdout_partition": "locked_test",
    "holdout_n_bootstrap": 1000,
    "holdout_ci": 0.95,
    "holdout_stratified": true
  }
}
```

The partition contract already validates that hold-out samples do not overlap `development_train` at config-parse time. The held-out samples must also appear in the project's control/disease cohorts (with their true labels) so that each hold-out sample can be assigned a class for labeled metrics.

### Disjointness Guarantee (Exclusion at Freeze)

For the held-out claim to be valid, the frozen model must not have been trained on the hold-out samples at **any** stage. When `holdout_exclude_from_training=true` (default):

- `--stability` excludes the hold-out partition from the Monte Carlo cohort pool, so feature selection (DMP discovery) sees only the active samples;
- `--freeze` filters the hold-out samples out of the production cohort list files before building centroids/detector/classifier, and records the excluded set together with each sample's recovered class label in `monte_carlo_runs/production/holdout_manifest.json`.

Workflow 3 preflights that manifest and refuses to run if any current hold-out sample was not excluded, so a false held-out claim cannot be produced silently. Because freeze removes the hold-out samples from the cohort CSVs, their class labels can no longer be read from those files; Workflow 3 therefore recovers labels from the manifest's `holdout_class_map` recorded at freeze time.

### Bootstrap Distribution of QC Metrics

The frozen model scores the hold-out batch **once** (a single `methyl-predictor` pass), producing per-sample true labels, predictions, and class probabilities. The per-sample results are then resampled (stratified by class by default) `holdout_n_bootstrap` times. For each resample the following are recomputed:

- balanced accuracy, sensitivity, specificity, $F_1$, ROC-AUC (binary), or
- balanced accuracy, macro recall, macro specificity, macro $F_1$, macro one-vs-rest AUC, plus control-vs-pooled-disease screening sensitivity/specificity/$F_1$/AUC (multiclass).

Each metric is summarized by its mean, standard deviation, two-sided bootstrap confidence interval at level `holdout_ci`, and percentiles. ROC-AUC is reported as NaN (and excluded from a summary) for any resample in which only one class is present.

### Steps

```bash
source .venv/bin/activate
# 1. Build the frozen model with hold-out excluded from training:
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --stability
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --freeze
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --model
# 2. Evaluate the frozen model on the designated hold-out batch:
methyl-validation --project configs/project_Healthy_vs_PCa1-4-CG.json --holdout-eval
```

Outputs under `monte_carlo_runs/holdout_batch/`:

- `holdout_metrics_bootstrap.json` — point estimate plus per-metric distribution summary,
- `holdout_metrics_distribution.csv` — one row per bootstrap resample (for custom plots),
- `holdout_metrics_distribution.html` — KDE/ECDF of each metric,
- `predictor/` — the raw per-sample predictions and `validation_metrics.json`.

### Caveat

Bootstrapping characterizes uncertainty due to the finite size and composition of the *provided* hold-out batch. It is not a substitute for prospective external validation on an independent population; it quantifies how much the reported metrics would vary if the same held-out batch had been sampled slightly differently.

---

## Diagnostic: DMP Coverage {#sec-diag-dmp-coverage}
**Symptom.** `sample_dmp_coverage.dmps_used_fraction_median < 0.5` in `validation_metrics.json`. Values below 0.3 indicate severe degradation.

**Cause.** The centroid/detector `min_coverage` (e.g. 4) is lower than the **MethylClassifier** per-locus coverage requirement for new samples, so many DMP loci are treated as missing in the feature matrix.

**Fix.** In profile **`actionConfig.classifier`** (MethylClassifier config), set `min_coverage` to match centroid `base_config.min_coverage` (see classifier documentation for the exact field name in your layout).

**Verification.** Re-run Workflow 2. `dmps_used_fraction_median` should rise to 0.6–0.9 for typical WGBS data.

---

## Diagnostic: Class Imbalance {#sec-diag-class-imbalance}
**Symptom.** The confusion matrix shows near-perfect recall for the majority class (e.g. healthy/control) and near-zero recall for minority classes (disease stages). Balanced accuracy is close to `1/K` (random chance).

**Cause.** The training cohort has substantially more samples in one class. For example, 86 healthy vs 25–36 disease samples per stage (41% healthy in the example project). The multinomial logistic regression learned head defaults to the majority class when the histogram scores are ambiguous (which is common when DMP coverage is low).

**Fix.** In profile `actionConfig.detection`, add:

```json
"multiclass_train_learned_head": true,
"multiclass_learned_class_weight": "balanced"
```

The `balanced` setting computes $w_k = n_{\text{total}} / (K \cdot n_k)$, penalizing majority-class errors proportionally less than minority-class errors. See [§ classifier class weight](04-methylclassifier.md#sec-classifier-class-weight) for the full derivation.

---

## Diagnostic: Sklearn Version Mismatch {#sec-diag-sklearn-version}
**Symptom.** Warning messages during prediction: `InconsistentVersionWarning: Trying to unpickle estimator LogisticRegression from version X.Y.Z when using version A.B.C`.

**Cause.** The `multiclass-classifier.pkl` was serialized with a different version of scikit-learn than the one currently installed.

**Diagnosis.** Check the `sklearn_version` field in the model PKL metadata:

```python
import pickle
with open("monte_carlo_runs/production/classifiers/multiclass-classifier.pkl", "rb") as f:
    model = pickle.load(f)
print(model["metadata"].get("sklearn_version"))  # version used at training time
```

Compare to the current installed version:

```python
import sklearn; print(sklearn.__version__)
```

**Fix.** Re-run the `--freeze` step with the current sklearn version. The logistic regression head will be retrained and the new PKL will record the correct version. New model PKLs produced after the fix always record `sklearn_version` in their metadata.

---

## Choosing Workflow Parameters {#sec-workflow-parameters}
### `train_fraction`

Higher values (0.8–0.9) leave less data for validation but produce more powerful centroids in training. For small cohorts (total $n < 50$ per class), prefer 0.8. For large cohorts, 0.9 is reasonable.

### `n_iterations`

More iterations reduce the variance of the empirical performance summaries. 20–50 iterations is typically sufficient to characterize the mean and standard deviation of balanced accuracy. 100+ iterations are useful for stable percentile estimates (e.g. p5, p95).

### `stability_dmp_freq`

Higher values (0.8–1.0) produce smaller, more reproducible panels but may miss positions that are biologically informative but variable across splits. Lower values (0.5–0.7) retain more positions at the cost of including split-sensitive noise. A good starting point is 0.7.

### `stability_min_balanced_accuracy`

Setting this parameter (e.g. to 0.6) excludes iterations with poor validation performance from the DMP frequency count. This can improve panel quality by excluding outlier runs where the centroid construction or detection failed to find informative DMPs. It also reduces the effective `R*` and thus increases the variance of $\hat{f}(d)$. Use with caution for small `n_iterations`.
