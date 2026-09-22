# MethylCluster Theoretical Foundation

> **ARCHIVED (2026-06):** `methylcluster` is deprecated and not on the ECDF supervised production path. Use mapper/enricher interpretation instead. Package retained for archaeology only.

The legacy workflow note for this package is documented in the root canonical guide: [`README.md`](../../../README.md).

This local page is intentionally brief so that it does not drift from the code.

## Scope

`methylcluster` is an exploratory clustering layer. It is **not** part of the ECDF-only supervised detector-classifier path. The implemented methods include:

- pairwise sample dissimilarities obtained by aligning loci and averaging per-locus distances,
- standard clustering algorithms such as HDBSCAN, hierarchical clustering, and k-means over those dissimilarities,
- a custom centroid-based clustering routine that scores samples with beta log-densities.

## Method Status

- **Principled**: HDBSCAN, hierarchical clustering, silhouette scoring, k-means.
- **Approximate**: pairwise methylation distances averaged over varying overlap sets.
- **Heuristic**: centroid reassignment rules, cluster rescue logic, and fallback selection rules.
- **Experimental**: the Dirichlet-process-style branch is incomplete and should not be cited as an implemented production method.

## Key Code Paths

- `methyl_cluster/distance_matrix.py`
- `methyl_cluster/cluster.py`
- `methyl_cluster/centroid_manager.py`
- `methyl_cluster/methyl_cluster_dp.py`
