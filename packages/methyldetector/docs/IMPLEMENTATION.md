# MethylDetector Implementation

This document describes how MethylDetector is implemented on top of **MethylUtils**: centroid comparison, statistics, ECDF scoring, filtering, and classifier training.

---

## Architecture

- **MethylUtils** owns centroid comparison math: KS-on-ECDF or histogram-derived Mann-Whitney U, Storey q-values, ECDF overlap, `effect_size`, heterogeneity (`tau2`), and `ECDFClassifier`.
- **MethylDetector** owns the pipeline: config, per-chromosome/context orchestration, staged filtering, optional rescue track, optional validation helpers, classifier invocation, and exports.

```mermaid
flowchart LR
    Config[JSON Config]
    Detector[MethylDetector]
    Pair[MethylCentroidPair]
    Test[KS_ECDF_or_MWU]
    FDR[Storey_qvalue]
    ECDF[Continuous ECDF overlap]
    Effect[effect_size formula]
    Filter[Per-context effect_size_coverage]
    Holdout[Repeated held-out BA]
    Clf[ECDFClassifier]
    Out[CSV and Classifier model]

    Config --> Detector
    Detector --> Pair
    Pair --> Test
    Test --> FDR
    FDR --> ECDF
    ECDF --> Effect
    Effect --> Filter
    Filter --> Holdout
    Holdout --> Clf
    Clf --> Out
```

---

## MethylCentroidPair (methyl_utils)

### `compare_centroids(centroid1, centroid2, position_subset=None)`

The primary comparison entry point.

- Aligns centroids to common positions; applies `position_subset` to restrict the comparison when a pre-filter has already reduced the candidate set.
- Runs the configured significance test on every aligned position in the MethylDetector path: `ks_ecdf` by default, `mann_whitney` as the alternative.
- Applies Storey q-value correction on the resulting p-values.
- Returns a DataFrame with columns: `position`, `p_value`, `q_value`, `mean1`, `mean2`, `delta_mean`, `delta_sign`, `variance1`, `variance2`, `tau2_1`, `tau2_2`, `n1`, `n2`, `overlap_approx`, `effect_size`, `dist` (always `DIST_ECDF = 5`).

### `load_and_align(path1, path2, min_coverage=...)`

Loads centroid HDF5 files, intersects positions, and applies the coverage filter. Returns `(centroid1, centroid2, common_positions)`.

### `validate_centroid_parameters(centroid1, centroid2)`

Pre-run sanity check (N, means, variances, ECDF availability).

### `extract_methylation_fractions(...)`

Extracts methylation fractions for real validation samples at the selected DMP positions.

---

## MethylUtils modules used

| Module / symbol | Role in MethylDetector |
|----------------|------------------------|
| `MethylCentroidPair` | All centroid comparison (see above) |
| `statistical_tests.mann_whitney_from_bin_counts` | Per-position non-parametric rank test reconstructed from centroid histograms |
| `statistical_tests.dl_heterogeneity` | DerSimonian-Laird-style `tau2` heterogeneity estimate |
| `statistical_tests.ecdf_effect_size` | Continuous ECDF overlap + final `effect_size` |
| `statistical_tests.ecdf_overlap_integral` | Integration of min(f1, f2) |
| `statistical_tests.effect_size_from_components` | `\|delta_mean\| * (1-overlap) * exp(-λ*(√v1+√v2))` |
| `core.distribution_views.ECDFView` | PCHIP-based CDF/PDF, built lazily for DMP positions only |
| `ecdf_classifier.ECDFClassifier` | PCHIP PDF log-likelihood classifier |
| `load_from_h5` | Loading centroid H5 for bin_counts extraction at classifier build |
| `gpu_detection`, `memory_manager` | GPU and memory handling |
| `core.methyl_frame.MethylSample` | Validation sample loading |
---

## Detection Pipeline (per chromosome × context)

### 1. Pre-filter (cheap delta_mean gate)

Computes `|mean1 - mean2|` from centroid means (`Sx/N`) at positions already shared by both centroids. Positions below `delta_mean_reduction` are removed before any expensive computation.

**Why**: The histogram-derived Mann-Whitney U stage plus later continuous ECDF overlap remain expensive at CHH scale. A position removed here would be discarded by the biological filter anyway, so pre-filtering costs nothing biologically.

**Statistical note**: FDR is then applied to the pre-filtered subset. Q-values are therefore liberal relative to the full test set — a known screen-and-test trade-off acceptable for practical genomics.

### 2. Centroid alignment and coverage filter

`MethylCentroidPair.compare_centroids(..., position_subset=pre_filter_positions)` restricts the comparison to the pre-filtered set.

### 3. Statistical gate + FDR

Run the configured significance test on all pre-filtered positions:

- `ks_ecdf` (default): KS statistic on the precise ECDF / PCHIP view.
- `mann_whitney`: `mann_whitney_from_bin_counts` reconstructed from centroid histograms.

Then apply Storey q-values and retain positions with `q_value <= alpha`.

### 4. Lazy ECDFView construction

For the surviving DMP positions only, build two `ECDFView` objects:

```python
idx_in_c1 = np.searchsorted(pos1, dmp_positions)
idx_in_c2 = np.searchsorted(pos2, dmp_positions)
ecdf_view1 = ECDFView(bin_edges, bin_counts_c1[idx_in_c1], Sx1[idx_in_c1], N1[idx_in_c1], Sx2_1[idx_in_c1])
ecdf_view2 = ECDFView(bin_edges, bin_counts_c2[idx_in_c2], Sx2[idx_in_c2], N2[idx_in_c2], Sx2_2[idx_in_c2])
```

Both views use their **own correct per-centroid indices** so the PCHIP PDFs correspond to the right genomic positions in each centroid.

### 5. Continuous ECDF overlap and final effect_size

`ecdf_effect_size(delta_mean, var1, var2, ecdf_view1, ecdf_view2, sequential_indices, lambda_var, grid_size)` — the pre-sliced views mean the indices are simply `0, 1, 2, …`.

Output: `overlap`, `effect_size`, `effect_size_reliability` added to the DMP DataFrame.

### 6. Biological filter

Within each context independently, sort by `effect_size` descending and keep the minimum prefix whose cumulative effect mass reaches `effect_size_coverage`.

Optional rescue track: after the same statistical comparison, non-significant loci can be selected separately with `biological_only_effect_size_coverage`; these rows are flagged with `statistical_dmp=False` / `biological_dmp=True` so they are never confused with confirmed statistical DMPs.

### 7. Export handoff: discovery vs classifier panels

After biological filtering and `_compute_biological_importance`, the pipeline builds:

- **Classifier panel** (`_classifier_dmps_from_sorted`): effect-size elbow trim by default; optional `classifier_dmp_selection=featurecuts_validation` runs `_optimize_dmps_featurecuts` on configured validation samples to pick top-*k* by balanced accuracy.
- **Discovery export** (`dmp_export_mode=dual`): `dmps-{chrom}-discovery.csv` is the full biologically filtered, importance-sorted table (no elbow unless `discovery_dynamic_dmp_cutoff_enabled=true`). `dmps-{chrom}-classifier.csv` mirrors the classifier panel. `dmp-export-{chrom}.meta.json` records counts and options.
- **Unified mode** (`dmp_export_mode=unified`): single `dmps-{chrom}.csv` for the classifier panel; if fewer rows remain than `min_dmps_for_export` but more biological DMPs exist, the CSV is widened to that minimum while the pickle still uses the elbow subset.

See also `docs/theory/chapters/03-methyldetector.md` (Discovery versus prediction exports).

---

## Classifier Training

MethylDetector trains an `ECDFClassifier` on the selected biological DMPs.

### Building the classifier

At the three call sites (centroid self-check, validation, model save), MethylDetector:

1. Normalises `effect_size` → `weight` (divided by max, clipped to `[1e-6, 1]`).
2. Calls `_extract_bin_counts_for_dmps(dmps_df)` to pull the `bin_counts` histograms for the selected DMP positions from the cached centroid H5 data.
3. Calls `ECDFClassifier.from_dataframe(dmpDF, bin_edges, bc1, bc2, temperature=...)`.

### `_extract_bin_counts_for_dmps(dmps_df)`

Groups DMPs by chromosome × context, loads the corresponding centroid H5 files once per group, caches the full histogram tables in memory, and extracts `bin_counts` at the DMP positions via `np.searchsorted`. Returns `(bin_edges, bc1, bc2)`.

### ECDFClassifier prediction

```
mean_log_L(class_k | x) = Σ_i w_i · clamp(log F'_k_i(x_i), cap) / Σ_i w_i
P(class_k | x) = softmax(mean_log_L / T_eff)
T_eff = temperature * sqrt(n_effective)
```

Per-position PCHIP PDF values are pre-computed into a dense lookup table at construction time; prediction uses `np.interp` (no Python loop over samples). The classifier caps extremely small per-position log-PDF values, averages the weighted log-likelihood across available positions, and scales temperature by the effective number of weighted loci so large DMP sets remain numerically stable.

---

## Model Serialisation

The classifier model is saved as a `.pkl` package containing:

- `classifier`: `ECDFClassifier` instance (holds `bin_edges`, `bin_counts_c1/c2`, `weights`, `directions`, `temperature`).
- `dmpDF`: DataFrame with `pos`, `weight`, `context`, `delta_sign`.
- `metadata`: version, `classifier_type` (`"ECDFClassifier"`), contexts, config, centroid file references.

---

## Summary

| Layer | Component | Role |
|-------|-----------|------|
| MethylUtils | `MethylCentroidPair` | Centroid load, align, compare (KS or Mann-Whitney + Storey q-values + initial effect_size + tau2) |
| MethylUtils | `statistical_tests` | KS support, Mann-Whitney U, Storey q-values, ecdf_overlap_integral, effect_size_from_components |
| MethylUtils | `ECDFView` | Lazy PCHIP CDF/PDF for DMP positions only |
| MethylUtils | `ECDFClassifier` | PCHIP PDF log-likelihood classifier, save/load |
| MethylUtils | GPU/memory | Device selection, memory management |
| MethylDetector | `MethylDetector` | Config, orchestration, pre-filter, biological filter, optional rescue/validation helpers, classifier invocation, export |

For theoretical background, see [THEORY.md](THEORY.md) and the canonical theory book at [../../../docs/theory/README.md](../../../docs/theory/README.md).
