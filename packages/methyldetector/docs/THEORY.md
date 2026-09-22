# MethylDetector Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/03-methyldetector.md`](../../../docs/theory/chapters/03-methyldetector.md).

## Scope

`methyldetector` identifies differential methylation positions by combining:

- centroid alignment,
- locus-wise nonparametric statistical screening,
- multiple-testing correction,
- biological effect-size ranking,
- optional effect-mass filtering before classifier export.

## Method Status

- **Principled**: ECDF-based KS screening, q-value adjustment, detector-to-classifier feature export.
- **Approximate**: histogram-based Mann-Whitney and grid-based overlap/KS evaluation when those paths are used.
- **Heuristic**: effect-size ranking, cumulative effect-mass selection, optional biological rescue tracks.

## Key Point

The package separates statistical evidence from biological prioritization. That distinction should remain explicit in any publication or downstream report.

## Key Code Paths

- `methyl_detector/core/methyldetector.py`
- `methyl_utils/methyl_centroid_pair.py`
- `methyl_utils/statistical_tests.py`
- `methyl_utils/ecdf_classifier.py`
