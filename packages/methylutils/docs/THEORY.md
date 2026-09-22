# MethylUtils Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/01-methylutils.md`](../../../docs/theory/chapters/01-methylutils.md).

## Scope

`methylutils` is the shared mathematical layer for:

- centroid sufficient statistics,
- ECDF reconstruction from histogram counts,
- KS and histogram-based Mann-Whitney testing,
- Storey q-values and p-value aggregation,
- ECDF overlap and canonical effect-size ranking,
- ECDF-based classification scores.

## Method Status

- **Principled**: centroid moment calculations, Storey q-values, standard asymptotic testing, weighted ECDF log-likelihood scoring.
- **Approximate**: grid-based KS, overlap integration, histogram-reconstructed Mann-Whitney.
- **Heuristic**: biological effect-size ranking and some numerical stabilizers.
- **Auxiliary**: beta-based metrics remain available for downstream consumers such as clustering, but they are not the canonical detector-classifier path.

## Key Code Paths

- `methyl_utils/core/distribution_views.py`
- `methyl_utils/core/centroid_builder.py`
- `methyl_utils/statistical_tests.py`
- `methyl_utils/methyl_centroid_pair.py`
- `methyl_utils/ecdf_classifier.py`

## Optional M-value residualization

Train-only M-value residualization against label-free covariates is an **opt-in** transform used only when `residualizeCoefDir` is bound. Canonical math and isolation rules: [theory ch.02](../../../docs/theory/chapters/02-methylcentroid.md#sec-centroid-mvalue-residual) and [ch.12 pre-MC covariates](../../../docs/theory/chapters/12-two-workflows.md#sec-pre-mc-covariates). Per-confounder mechanics (Hannum 2013 age score, smoking, BMI, CRP, Ω ALR): [M-value residualization implementation](../../../docs/implementation/mvalue-residualization.md).
