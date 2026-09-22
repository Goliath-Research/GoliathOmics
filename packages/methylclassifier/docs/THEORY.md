# MethylClassifier Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/04-methylclassifier.md`](../../../docs/theory/chapters/04-methylclassifier.md).

## Scope

`methylclassifier` applies one or more binary ECDF classifiers to new samples and supports:

- binary classification,
- chromosome-weighted multi-chromosome classification,
- one-vs-rest multiclass fusion,
- aggregated-feature OvR bundles (`classifier_type: ecdf_aggregated_one_vs_rest`) over observed-hybrid feature families,
- pairwise control aggregation and bipartite aggregation,
- optional probability calibration.

## Method Status

- **Principled**: binary ECDF density scoring with explicit class priors (`p(X|c) * p(c)`), optional dependence-aware block aggregation, and optional calibration.
- **Approximate**: chromosome fusion inherits the assumptions of its component binary models.
- **Heuristic / Versioned**: OvR fusion and geometric-mean control aggregation remain engineered reductions, but are now versioned via `ovr_inference_version` + `ovr_fuse_mode` metadata for reproducible interpretation.

## Key Point

The binary head is the cleanest part of the model and now has an explicit posterior contract:

- feature densities from ECDF/PCHIP per class,
- weighted log-likelihood aggregation,
- explicit class priors,
- optional post-hoc calibration.

Multiclass and multi-chromosome behavior remain an engineered ensemble on top of that core; inference semantics are explicitly versioned in package metadata.

## Aggregated ECDF OvR Semantics

`ecdf_aggregated_one_vs_rest` packages use observed-hybrid engineered features (for example gene and structural families) instead of raw DMP coordinates as the ECDF domain. Training stores:

- feature schema and deterministic per-feature effect-size-aware weights,
- per-feature transform parameters (fill + min/max clip to `[0,1]` for ECDF histograms),
- one binary ECDF head per class.

The observed-hybrid family contract is controlled by `feature_family_set` (`dmp`, `gene`, `structural`, `dmp+gene`, `dmp+structural`, `hybrid-all`) and is expected to match model-bundle training metadata at inference.

Inference returns posterior `prob_class*` values after OvR fusion and optional `evidence_class*` diagnostics. `evidence_class*` values are pre-softmax OvR log-evidence and are **not** p-values.

## Key Code Paths

- `methyl_classifier/core/classifier.py`
- `methyl_classifier/core/multiclass_ovr.py`
- `methyl_utils/ecdf_classifier.py`
