# MethylCentroid Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/02-methylcentroid.md`](../../../docs/theory/chapters/02-methylcentroid.md).

## Scope

`methylcentroid` builds cohort-level centroids that persist:

- per-locus sufficient statistics such as `N`, `Sx`, and `Sx2`,
- count summaries such as `Sm`, `Su`, `Sc2`, and `Swx2`,
- empirical histogram counts used later for ECDF reconstruction.

## Method Status

- **Principled**: aggregation of methylation fractions and counts into reusable sufficient statistics.
- **Principled preprocessing**: optional binomial thinning for coverage capping.
- **Heuristic**: automatic cap selection and cohort-management policies around add/remove deltas.

## Key Point

This package does not fit a parametric methylation model. It constructs empirical cohort summaries for the downstream nonparametric detector and classifier path.

## Key Code Paths

- `methyl_centroid/methyl_centroid.py`
- `methyl_utils/core/centroid_builder.py`
- `methyl_utils/core/methyl_frame.py`
