# Model Creation and Validation {#sec-model-creation-validation}
This chapter consolidates the implementation-facing view of model training and validation across the current MethylPipeline stack.

It complements:

- [§ workflow model creation](12-two-workflows.md#sec-workflow-model-creation) (workflow theory),
- [§ workflow prediction](12-two-workflows.md#sec-workflow-prediction) (predictor-only evaluation),
- **Usage manual** ch.05–08 for operator commands ([`docs/usage/`](../../usage/index.md)).

---

## Scope

Model creation in MethylPipeline has two layers:

1. **Biology-first feature stabilization** (Monte Carlo stability + freeze), and
2. **Final model fitting and evaluation** (`--model` backend path).

The validated production sequence is:

- `--stability` (MC centroid -> detector recurrence),
- `--freeze` (single all-sample run with fixed panel + interpretation),
- `--model` (backend-specific train/predict),
- optional `--predictor-only` for repeated frozen-model performance distribution.

---

## Training and Validation Lifecycle

```mermaid
flowchart TD
  A[Project config + cohort definitions]
  B[Stage stability: MC centroid to detector]
  C[Stable panel from recurrence thresholding]
  D[Stage freeze: all-sample run with fixed panel]
  E[mapper enricher progression optional]
  F[Stage model: backend training + predictor]
  G[Production metrics + model artifacts]
  H[Optional repeated frozen-model predictor evaluation]
  A --> B --> C --> D --> E --> F --> G --> H
```

For CLI entry points and artifact gates, see Usage ch.05–08.

---

## Backend Roles

Current `--model` backend semantics:

- **`ecdf`**: `methyl-classifier` -> `methyl-predictor` (Bayesian/ECDF path, DMP-centric).
- **`tabular_sklearn`**: model bundle -> tabular train -> tabular predictor.
- **`generative_hybrid`**: model bundle -> latent generative train -> generative predictor.

Covariate fusion is backend-specific:

- `ecdf`: methylation-only first stage (DMP / gene / aggregated); optional second-stage stacker fuses ECDF probabilities with covariates (and optionally observed-hybrid features when `ecdf_second_stage_enabled`).
- `tabular_sklearn` / `generative_hybrid`: methylation / observed-hybrid features plus optional covariates.

---

## Covariate-Aware Validation Contract

When covariates are enabled for tabular/generative backends (or the ECDF second-stage stacker):

- join key is sample basename against `covariate_id_column`,
- numeric/ordinal/categorical role assignment is fixed at training and reused at inference,
- numeric imputation + optional standardization are frozen from training statistics,
- ordinal columns use preserved label→code mappings (`covariate_ordinal_maps`) with unknown-value fallback — **one numeric feature per ordinal column** (no redundancy),
- categorical (nominal) encoding uses frozen vocabulary with an unknown-category bucket and **drops one reference level** so $L$ levels become $L-1$ one-hot columns,
- composition groups (ALR) and this non-redundant encoding apply to `tabular_sklearn`, `generative_hybrid`, and the ECDF second-stage stacker alike,
- shape/schema consistency is enforced before prediction.

This contract keeps training and inference feature spaces aligned while preserving reproducibility across reruns.

### Composition (simplex) covariates and class probabilities

Any feature set whose parts sum to one (a *composition*) carries a redundant
degree of freedom: with $K$ parts summing to 1, only $K-1$ are free. Feeding all
$K$ into a linear model creates perfect collinearity, and the parts are bounded
to $[0,1]$ rather than the real line. MethylPipeline resolves both problems with
one rule: every simplex is encoded by the **additive log-ratio (ALR)**. For
parts $p_1,\dots,p_K$ and a reference part $p_r$,

$$
\mathrm{alr}_i = \log\frac{p_i + \varepsilon}{p_r + \varepsilon}, \qquad i \neq r,
$$

yielding $K-1$ unbounded coordinates and dropping the reference. A small
pseudocount $\varepsilon$ guards against $\log 0$.

Two simplex sources use the same encode rule:

- **Binary ($K=2$).** Emit the closed non-reference probability $p$ (ECDF:
  `prob_class1`; composition groups: `p_<nonref>`). No log-ratio and no
  $\varepsilon$ are required. Dual raw columns are never stacked.
- **Multiclass ($K>2$).** ALR vs the reference as above (ECDF requires
  `ecdf_second_stage_probability_epsilon`; composition groups require
  `pseudocount`).

**Cell-type fractions and other declared groups.** Operators declare each
composition with `covariate_composition_groups` (name, ordered columns,
reference defaulting to the last column, optional `pseudocount` for $K=2$,
required for $K>2$, and whether encoded coordinates are standardized). Parts
must be disjoint from each other and from numeric/ordinal/categorical roles, so
raw multi-part proportions never enter the design matrix. Deconvolution outputs
(Houseman or HiTIMED, see [§ methyldeconv](07a-methyldeconv.md#sec-methyldeconv)) are the primary example.

ALR coordinates live on $\mathbb{R}$, so standardizing them (the default) is
meaningful; binary probabilities stay on $[0,1]$ and are not ALR-transformed.

---

## Outputs to Review

For model readiness decisions, review at least:

- `monte_carlo_runs/stability/stable_dmps_production.csv`,
- `monte_carlo_runs/production/production_summary.json`,
- `monte_carlo_runs/production/classifiers/*`,
- `monte_carlo_runs/production/predictors/validation_metrics.json`,
- `monte_carlo_runs/all_metrics.csv` and `metrics_summary.json` (or predictor-only equivalents).

For covariate-aware backends, also review backend metadata and preprocessing artifacts under the production classifier directory.

---

## Configuration Audit for Accuracy-First Modeling {#sec-model-config-audit}
The latest workflow (`--stability` -> `--freeze` -> `--model` with optional `--predictor-only`) is now mature enough that some configuration surfaces can be simplified without losing capability.

### Deprecation candidates (proposed)

These are practical candidates for staged deprecation in a future major release:

| Candidate key / pattern | Current status | Why deprecate | Replacement |
|---|---|---|---|
| `actionConfig.validation.run_mapper_and_enricher` | Marked legacy in config reference | Stability iterations now run centroid+detector only; mapper/enricher belong to `--freeze` | Remove flag; always run mapper/enricher in `--freeze` when enabled in project steps |
| `actionConfig.validation.skip_enricher` | Legacy companion to `run_mapper_and_enricher` | Semantics overlap with step-level enricher control and creates branchy MC behavior | Drop from validation block; control enricher directly in freeze-stage execution policy |
| `actionConfig.validator` (alias) | Deprecated alias for `actionConfig.predictor` | Duplicates predictor namespace and complicates resolver logic | Use only `actionConfig.predictor` |
| Flat cohort schema (`group1` / `group2` / top-level `groups`) | Backward-compatible path | New control/disease/comparisons schema is clearer and supports multiclass/stage workflows natively | Use `control`, `disease`, and explicit `comparisons` |

Deprecation strategy recommendation:

1. Keep warnings in the next minor release.
2. Add a migration helper command that rewrites legacy project JSON fields.
3. Remove legacy keys in the next major version after one full release cycle.

### Accuracy-first project profile

For the best downstream predictive performance, favor stable panel discovery first, then conservative production fitting:

- **Stability stage quality**: use enough iterations (`n_iterations >= 50`) and apply run filtering (`stability_min_balanced_accuracy`) only when sample count supports it.
- **Coverage consistency**: keep centroid, detector, and **MethylClassifier** `min_coverage` aligned so DMP loci visible during discovery stay observable in new samples.
- **Panel robustness over aggressiveness**: use detector FeatureCuts in stability/freeze (`classifier_dmp_selection="featurecuts_validation"`) with a minimum panel size floor (`min_selected_dmps`) so high BA does not come from brittle tiny panels.
- **Generalization-first model selection**: prioritize `optimization_validation` (held-out fold) for backend and parameter decisions; use `training_fold_validation` only as overfit diagnostic.
- **Covariate discipline**: only enable covariates when join coverage is high and schema is stable; otherwise default to pure ECDF path to avoid leakage/shift from weak sidecars.

### Reference tuning template (binary ECDF-first)

```json
"actionConfig": {
  "validation": {
    "train_fraction": 0.8,
    "n_iterations": 50,
    "seed": 42,
    "run_stability": true,
    "stability_dmp_freq": 0.7,
    "stability_min_balanced_accuracy": 0.6,
    "model_backend": "ecdf"
  },
  "detection": {
    "min_coverage": 4,
    "validation_split_ratio": 0.2,
    "validation_n_repeats": 5,
    "classifier_dmp_selection": "featurecuts_validation",
    "min_selected_dmps": 500,
    "dynamic_dmp_cutoff_enabled": true,
    "effect_size_coverage": 0.95,
    "enable_platt_calibration": true
  }
}
```

Use this as a starting point, then tune in this order:

1. stabilize panel recurrence (`stability_dmp_freq`, `n_iterations`),
2. verify holdout-vs-training gap (`optimization_validation` vs `training_fold_validation`),
3. only then tune panel size/FeatureCuts constraints for marginal BA gains.

---

## Distributed Monte Carlo on shared storage {#sec-distributed-mc-shared-storage}
Large `n_iterations` in stability discovery are often limited by **wall time** on a single host, not by statistical need. Sharded Monte Carlo requires shared read–write access to the same `output_base` tree. The queue workflow is a **plan → workers → aggregate** pattern.

**Operator documentation:** [Usage ch.13](../../usage/13-distributed-methyl-validation.md) and [`packages/methylvalidation/docs/DISTRIBUTED_QUEUE.md`](../../../packages/methylvalidation/docs/DISTRIBUTED_QUEUE.md).

---

## Optional pipeline hyperparameter search {#sec-optional-hyperparameter-search}
The package implements a read-only **scalar objective** \(J(\theta)\) from completed runs, combining weighted validation metrics with optional panel-size terms. Discovery/stability optima and calibration metrics can **conflict**; a **two-stage** search is often safer than a single ad hoc \(J\).

**Theory and formulas:** [`packages/methylvalidation/docs/HYPERPARAMETER_SEARCH.md`](../../../packages/methylvalidation/docs/HYPERPARAMETER_SEARCH.md). **Operator steps:** [Usage ch.15](../../usage/15-optional-hyperparameter-search.md).
