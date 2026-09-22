# MethylCentroid Implementation

## Layers

The implementation is split across two packages:

- `methyl_centroid`: the runner/orchestrator, CLI, config models, batch logic,
  and project resolution.
- `methyl_utils`: the builder, centroid data object, HDF5 I/O, GPU utilities,
  and downstream ECDF comparison helpers.

## Build Pipeline

```text
user config / CLI / project
  -> methyl_centroid.MethylCentroid
  -> methyl_utils.core.centroid_builder.MethylCentroidBuilder
  -> methyl_utils.core.methyl_frame.MethylCentroid
  -> HDF5 centroid ({chrom}-{ctx}.h5)
```

## Runner Responsibilities

`methyl_centroid.MethylCentroid` is responsible for:

- resolving sample directories to `{sample_dir}/{chrom}-{ctx}.h5`
- applying the public cohort contract:
  `samples`, `add_samples`, `remove_samples`
- validating that `binned_stats_bins >= 1`
- invoking the builder
- writing metadata and the sidecar config

The active cohort is resolved deterministically before the build:

```text
effective_samples = samples - remove_samples + add_samples
```

That resolved cohort is then processed for the current chromosome/context.

## CPU Path

The CPU path is implemented with NumPy/pandas accumulation in
`methyl_utils.core.centroid_builder.MethylCentroidBuilder`.

Key points:

- samples are streamed one at a time with `add_sample(...)`
- positions are aligned into a sorted union
- sufficient statistics are accumulated per position
- per-position ECDF histograms are accumulated into `bin_counts`
- `finalize()` returns a CPU `MethylCentroid` data object

For large contexts, the runner can also use chunked recomputation over position
windows to limit peak memory use.

## GPU Path

The GPU path uses CuPy through the same builder interface:

- `use_gpu=True` requests GPU acceleration
- if GPU support is unavailable, the code falls back to CPU
- accumulation still targets the same sufficient statistics and histogram schema
- finalized centroids are converted back to CPU objects before persistence

The CPU and GPU paths intentionally share the same output contract so downstream
consumers do not need separate code paths.

Recent loader/indexing cleanup centralized position-index lookup in methylutils so
centroid chunked workflows and downstream classifiers share the same indexed HDF5
access semantics.

## Data Object

The persisted centroid type is
`methyl_utils.core.methyl_frame.MethylCentroid`.

It stores:

- `pos`, `tnc`
- `N`, `Sx`, `Sx2`
- `Sm`, `Su`, `Sc2`, `Swx2`
- required `binned_stats` (`bin_edges`, `bin_counts`) in memory

The HDF5 format stores:

- datasets under `methylation_data/`
- `methylation_data.attrs["bins"]`
- `methylation_data["bin_counts"]`

Load and save boundaries now require valid positive-bin ECDF data for centroids.

## Incremental Operations

There are two update layers:

- runner-level updates: resolve the final cohort from `samples`,
  `add_samples`, and `remove_samples`, then build that cohort for the target
  chromosome/context
- data-level updates: `MethylCentroid.add_sample(...)` and
  `MethylCentroid.remove_sample(...)` update a centroid object in memory

The runner re-applies `min_coverage` and `min_samples` semantics after sample
mutation so the result matches a full rebuild contract.

## Downstream Consumers

- `MethylCentroidPair` expects centroids with matching ECDF histogram bins and
  compares them with the ECDF-only path.
- `MethylDetector` builds on `MethylCentroidPair` and no longer accepts runtime
  distribution-selection knobs for centroid comparison. Its current ECDF
  utilities also reuse centroid `bin_counts`, while heterogeneity-oriented
  filters can consume stored `Sc2` and `Swx2`.
- `MethylValidation` can emit per-group centroid step overrides so each run
  passes explicit `samples`, `add_samples`, and `remove_samples` deltas.

## Config Defaults

The supported default for `binned_stats_bins` is `20`.

That default is aligned across:

- `MethylCentroidConfig`
- `MethylCentroidBuilder`
- direct CLI usage
- documentation examples

## Summary

Implementation-wise, the current contract is:

- one runner class
- one centroid data class
- one required ECDF histogram schema
- one supported centroid-comparison mode
