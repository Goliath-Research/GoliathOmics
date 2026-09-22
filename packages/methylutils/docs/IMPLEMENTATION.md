# MethylUtils Implementation

## Pipeline project config (cohorts)

- **`methyl_utils/pipeline_config.py`** — `ProjectConfig` / `GroupConfig` for control+disease projects, comparisons, resolved groups, optional **`cohort_hierarchy`**. Disease **`stages`** children are generic strata (not only TNM stage); see **[COHORT_TREE.md](COHORT_TREE.md)** and the v2 recursive-tree appendix there.

## Relevant modules for the ECDF effect-size pipeline

- `methyl_utils/statistical_tests.py`
  Owns the shared Mann-Whitney U test from centroid histograms, continuous ECDF overlap integration, canonical `effect_size` computation, and `lambda_var` optimization helper.
- `methyl_utils/core/distribution_views.py`
  Implements `ECDFView`, including PCHIP-based CDF/PDF evaluation and batched PDF sampling for overlap integration.
- `methyl_utils/methyl_centroid_pair.py`
  Loads and aligns centroids, computes per-position summary statistics, and delegates effect-size math to the shared helpers instead of maintaining a separate score formula.

## Shared contract

Downstream packages should treat the following as the canonical comparison flow:

1. Build or load centroids with `binned_stats`.
2. Run histogram-derived Mann-Whitney U testing on aligned centroid histograms.
3. Apply Storey q-value correction.
4. Compute continuous overlap only for the reduced set of positions that still matter.
5. Compute `effect_size` with the single canonical formula.

## Exported helper surface

The public `methyl_utils` package now exports:

- `mann_whitney_from_bin_counts()`
- `ecdf_overlap_integral()`
- `effect_size_from_components()`
- `ecdf_effect_size()`
- `optimize_lambda_var()`

These functions are the intended shared entry points for detector/explorer style analyses.

## M-value residualization and confounder scores

Canonical per-confounder write-up: [docs/implementation/mvalue-residualization.md](../../../docs/implementation/mvalue-residualization.md). Code map only here.

| Module | Role |
|--------|------|
| `methyl_utils/confounder_scores.py` | Label-free panel scores (`intercept + Σ w_i β_i`) |
| `methyl_utils/mvalue_residualize.py` | M-value round-trip; apply frozen coefficients |
| `methyl_utils/residualize_fit.py` | Train-only OLS; Neu-referenced ALR for Ω |
| `methyl_utils/residualize_config.py` | Typed `actionConfig.residualize` / `methylation_confounder_scores` |
| `methyl_utils/data/confounder_panels/` | Versioned GRCh38 JSON |

| Confounder | Packaged panel / source |
|------------|-------------------------|
| Smoking | `smoking_ahr_v1.json` |
| Epigenetic age | `hannum2013_v1.json` (Hannum 2013 71-CpG blood clock) |
| BMI / adiposity | `bmi_adiposity_v1.json` |
| Inflammation / CRP | `crp_inflammation_v1.json` |
| Leukocyte Ω | `cell_fractions.csv` via `residualize.composition_columns` |

`horvath2013_stub_v1.json` is a one-site test fixture, not the procedure default.

Entry points: `methyl-confounder-scores`, `methyl-residualize-fit`, `methyl-residualize-sensitivity`; worker actions `pipeline.methylation_confounder_scores` and `pipeline.residualize_fit`.

## Aggregated ECDF OvR package support

`methyl_utils.ecdf_aggregated_ovr` provides shared training/inference utilities for
observed-hybrid ECDF OvR bundles used by methylvalidation/methylclassifier flows.
It defines the package type contract (`ecdf_aggregated_one_vs_rest`), per-feature
weight handling, and OvR evidence-to-probability fusion helpers.
