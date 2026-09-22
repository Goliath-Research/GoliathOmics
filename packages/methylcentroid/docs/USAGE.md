# MethylCentroid Usage

## Environment

Activate the repository virtual environment before using the CLI:

```bash
source .venv/bin/activate
```

If the packages are not installed into the environment yet:

```bash
pip install -e packages/methylutils
pip install -e packages/methylcentroid
```

## Single Config

Use `MethylCentroidConfig` when you want to build one chromosome/context pair.

```json
{
  "laboratory": "example-lab",
  "disease": "example-disease",
  "group": "healthy",
  "batch": "2026-03",
  "chrom": "1",
  "ctx": "CG",
  "output_dir": "/path/to/output/centroids/healthy",
  "add_samples": [
    "/path/to/samples/sample_001",
    "/path/to/samples/sample_002"
  ],
  "remove_samples": [],
  "min_coverage": 4,
  "min_samples": 1,
  "binned_stats_bins": 20,
  "use_gpu": true,
  "verbose": true
}
```

Important points:

- The `samples` JSON field was **removed**. Pydantic rejects configs that still include it.
- **Full cohort (typical pipeline / first build):** put all sample directories in `add_samples` with `remove_samples` empty. If no centroid HDF5 exists yet for this chrom/context, the cohort is exactly `add_samples`.
- **Incremental add:** when `{output_dir}/{chrom}-{ctx}.h5` already exists, if every basename in `add_samples` is **disjoint** from `samples_used` in that HDF5, new paths are appended to the baseline cohort.
- **Incremental remove (and Monte Carlo deltas):** use non-empty `remove_samples` (and optional `add_samples`). Baseline membership is read from the existing HDF5 metadata (`samples_used` + `samples_base_path`), then removals and additions are applied.
- **Full cohort refresh when a centroid already exists:** pass the complete new list in `add_samples` (overlapping basenames with the existing centroid). That selects **replace** mode (same as always sending the full cohort from the pipeline resolver).
- Sample entries are directories, not `.h5` files.
- `binned_stats_bins` is required and must be positive.

Run it with:

```bash
python -m methyl_centroid.cli --config /path/to/config.json
```

## Batch Config

Use `BatchProcessingConfig` when the same cohort should be built across multiple
chromosomes and contexts.

```json
{
  "chromosomes": ["1", "2", "3"],
  "contexts": ["CG", "CHG", "CHH"],
  "base_config": {
    "laboratory": "example-lab",
    "disease": "example-disease",
    "group": "healthy",
    "batch": "2026-03",
    "chrom": "1",
    "ctx": "CG",
    "output_dir": "/path/to/output/centroids/healthy",
    "add_samples": [
      "/path/to/samples/sample_001",
      "/path/to/samples/sample_002"
    ],
    "remove_samples": [],
    "min_coverage": 4,
    "min_samples": 1,
    "binned_stats_bins": 20,
    "use_gpu": true,
    "verbose": true
  },
  "continue_on_error": true,
  "save_batch_summary": true
}
```

Run it with:

```bash
python -m methyl_centroid.cli --batch-config packages/methylcentroid/configs/multi_chromosome_config.json
```

## CLI Modes

### Config-driven CLI

```bash
python -m methyl_centroid.cli --config /path/to/config.json
python -m methyl_centroid.cli --batch-config /path/to/batch_config.json
python -m methyl_centroid.cli --config /path/to/config.json --no-gpu
```

### Project-driven CLI

This mode resolves cohorts from a pipeline project file and is the path used by
`MethylValidation`.

```bash
methyl-centroid --project /path/to/project.json --group group1
methyl-centroid --project /path/to/project.json --group group2
methyl-centroid --project /path/to/project.json --group all
```

For **hierarchical diseases** (`diseases.groups[].stages`), centroids are built per **resolved leaf** (e.g. `pca` + stage `pca1` → `pca_pca1`). Use `--group all` so every leaf runs. If you see **no samples** for a parent label like `pca` alone, the `stages` field was not loaded—install a current **methyl_utils** from this repo: `pip install -e packages/methylutils`.

When validation or another orchestrator wants to pass cohort deltas explicitly,
use a step override:

```bash
methyl-centroid \
  --project /path/to/project.json \
  --group group1 \
  --step-override /path/to/centroid_group1_override.json
```

Expected step override shape (no `samples` key; baseline comes from existing centroid HDF5 when `remove_samples` and/or disjoint `add_samples` are used):

```json
{
  "base_config": {
    "add_samples": ["/path/to/new/sample_003"],
    "remove_samples": ["/path/to/previous/sample_001"]
  }
}
```

`project.json` must not include `actionConfig.centroid.base_config.samples` (obsolete); the resolver raises a clear error if it is present.

## Python Class Usage

### Initial build

```python
from methyl_centroid import MethylCentroid

mc = MethylCentroid(
    laboratory="example-lab",
    disease="example-disease",
    group="healthy",
    batch="2026-03",
    chrom="1",
    ctx="CG",
    output_dir="./centroids/healthy",
    add_samples=[
        "/data/samples/sample_001",
        "/data/samples/sample_002",
    ],
    remove_samples=[],
    min_coverage=4,
    min_samples=1,
    binned_stats_bins=20,
    use_gpu=True,
)

results = mc.build_centroid()
print(results.final_centroid_path)
```

### Incremental update (after a centroid HDF5 exists)

Build an initial centroid, then run again with `add_samples` / `remove_samples` only (see rules above). Example: add one sample directory whose basename is not already in `samples_used`, or remove paths that match baseline members.

```python
mc = MethylCentroid(
    laboratory="example-lab",
    disease="example-disease",
    group="healthy",
    batch="2026-03",
    chrom="1",
    ctx="CG",
    output_dir="./centroids/healthy",
    add_samples=["/data/samples/sample_003"],
    remove_samples=["/data/samples/sample_001"],
    min_coverage=4,
    min_samples=1,
    binned_stats_bins=20,
)

mc.build_centroid()
```

The runner resolves the effective cohort from baseline HDF5 metadata plus `add_samples` / `remove_samples` (see `_plan_cohort_lists_for_runner` in `methyl_centroid.py`).

The authoritative cohort after a successful build is stored in HDF5 as:

- `samples_used` (directory basenames)
- optional `samples_base_path` when a common parent exists

The sidecar `{chrom}-{ctx}_config.json` no longer includes a `samples` field; it may list only pending `add_samples` / `remove_samples` (often empty after a run).

## Direct CLI Parameters

When not using a JSON config, the important direct flags are:

- `--chromosome`
- `--context`
- `--samples` (CSV of sample directory paths; passed through as `add_samples` in config)
- `--output-dir`
- `--min-coverage`
- `--binned-stats-bins`
- `--no-gpu`

The direct CLI path is best for ad hoc usage. Project and JSON configs are the
recommended entry points for reproducible runs.

## Outputs

Each successful build writes:

- `{chrom}-{ctx}.h5`
- `{chrom}-{ctx}_config.json`

The HDF5 centroid contains the required ECDF histogram data in
`methylation_data.attrs["bins"]` and `methylation_data["bin_counts"]`.

## Migration from older configs

- Remove every `"samples"` key from `MethylCentroidConfig` JSON, `BatchProcessingConfig.base_config`, `project.json` `actionConfig.centroid`, and step-override files.
- If the old value was the full cohort, move those paths into `add_samples` (and keep `remove_samples` as needed for deltas).

## Troubleshooting

- Missing sample files: each sample directory must contain `{chrom}-{ctx}.h5`.
- Import errors: activate `.venv` and ensure both `methylutils` and
  `methylcentroid` are installed in editable mode.
- GPU issues: rerun with `--no-gpu` or `use_gpu: false`.
- Comparison failures downstream: rebuild centroids with a positive
  `binned_stats_bins` value so `bin_counts` is present.
