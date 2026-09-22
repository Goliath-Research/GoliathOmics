# MethylCluster Usage

> **ARCHIVED (2026-06):** `methylcluster` is deprecated. Canonical interpretation: [mapper → enricher pipeline](../../../docs/architecture/pipeline-stages.md).

> Status: legacy/deprecated package. Not part of the active canonical production workflow.

## Canonical References

- Canonical workflow reference: [`README.md`](../../../README.md)
- Theory-book migration note: [`docs/theory/README.md`](../../../docs/theory/README.md)
- Implementation notes: [`IMPLEMENTATION.md`](IMPLEMENTATION.md)

## CLI Entry Points

The package exposes:

- `methyl-cluster`
- `methyl_cluster`

Both resolve to `methyl_cluster.cli:main`.

## Inputs

Typical runs require:

- sample directories or HDF5 methylation files,
- a chromosome and context selection strategy,
- clustering configuration such as algorithm choice, minimum cluster size, and optional fallback settings.

## Outputs

Depending on configuration, the package can emit:

- cluster assignments,
- pairwise distance matrices,
- centroid summaries,
- diagnostic plots and cluster visualizations.

## Interpretation

This package is best used for exploratory cohort structure analysis. Its outputs should not be interpreted as the result of a single calibrated probabilistic model.
