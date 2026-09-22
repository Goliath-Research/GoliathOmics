# MethylDetector User Manual

## Overview

MethylDetector detects differentially methylated positions (DMPs) between two methylation centroids (e.g. healthy vs disease), applies FDR correction and biological filtering, and can train a classifier on the selected DMPs. It is built on **MethylUtils** for centroid comparison and statistics (see [IMPLEMENTATION.md](IMPLEMENTATION.md)).

There are **two ways to run MethylDetector**:

1. **Docker container** — Run inside the MethylPipeline container (e.g. `methylpipeline`) with CUDA and dependencies provided.
2. **Local host with virtual environment** — Create a venv, install MethylUtils and MethylDetector, activate the venv, and run on the host.

Use one or the other; the CLI and Python API are the same once the environment is active.

---

## Setup 1: Docker container

Use this when you want a reproducible environment (e.g. shared GPU, CI) or the same setup as MethylCentroid.

**Prerequisites:** Docker; for GPU, NVIDIA Container Toolkit.

**1. Start the container**

From the MethylPipeline repo:

```bash
cd /path/to/MethylPipeline/docker
docker compose up -d
```

(Use the same container name as in your compose file; the rest of this section assumes `methylpipeline` and that the repo is mounted at `/workspace`.)

**2. Run MethylDetector**

Paths in your config must be valid **inside** the container (e.g. `/workspace/...`). From the host:

```bash
docker exec -w /workspace/packages/methyldetector methylpipeline \
  methyl-detector /workspace/packages/methyldetector/configs/your_config.json
```

Or run the CLI module:

```bash
docker exec -w /workspace/packages/methyldetector methylpipeline \
  python -m methyl_detector.cli /workspace/path/to/config.json
```

**Common options**

- `--verbose` / `-v`: verbose logging  
- `--log-file PATH`: write logs to a file  
- `--project PATH`: use pipeline project config (derives centroid and output paths)  
- `--step-override PATH`: JSON overrides for detector step  
- `--output-base PATH`: override project output base  
- `--centroid1-dir` / `--centroid2-dir`: override centroid dirs (with `--project`)  
- `--per-cancer-group`: run one detection per non-control group  
- `--multi-class-model`: build a native multiclass classifier from the per-comparison `dmps-*.csv` outputs and class centroids  
- `--learned-multiclass-head`: with `--multi-class-model`, fit a multinomial logistic layer on native histogram scores using the project’s training cohort lists  

In profile `actionConfig.detection` (same effect as the CLI flag / hyperparameters; multiclass keys are **not** passed to MethylDetector itself):

- `multiclass_train_learned_head` (bool): enable learned multinomial head when building the multiclass PKL  
- `multiclass_learned_logistic_C` (float, optional): sklearn `C` for the logistic head  
- `multiclass_learned_max_iter` (int, optional): LBFGS max iterations  
- `multiclass_learned_standardize` (bool, optional): standard-scale pre-softmax features before logistic regression  
- `multiclass_learned_random_state` (int, optional): random seed for the logistic fit  

Passing `--learned-multiclass-head` forces the learned head on even if the project flag is false.

Example with project:

```bash
docker exec -w /workspace methylpipeline \
  methyl-detector --project /workspace/project.json --per-cancer-group
```

---

## Setup 2: Local host with virtual environment

Use this when you run on the host (e.g. laptop or login node) and want to activate a virtual environment before running MethylDetector.

**Prerequisites:** Python 3.10-3.14; optional CuPy for GPU.

**1. Create a virtual environment**

From the **MethylPipeline repository root** (canonical environment directory is `.venv`):

```bash
cd /path/to/MethylPipeline
python3.14 -m venv .venv
```

**2. Activate the virtual environment**

```bash
source .venv/bin/activate
```

On Windows: `.venv\Scripts\activate`. After activation, the prompt usually shows `(.venv)`.

**3. Install MethylUtils (required dependency)**

MethylDetector depends on MethylUtils. Install it first:

```bash
cd /path/to/MethylPipeline/packages/methylutils
pip install -e .
```

**4. Install MethylDetector**

```bash
cd /path/to/MethylPipeline/packages/methyldetector
pip install -e .
```

**5. Optional: GPU support**

If you have CUDA and want GPU acceleration:

```bash
pip install cupy-cuda12x   # adjust to your CUDA version (e.g. cupy-cuda11x)
```

**6. Run MethylDetector**

With the virtual environment **activated** (`source .venv/bin/activate` from the repo root), use the CLI from any directory. Paths in the config are on the **host**; you do not use a container.

```bash
methyl-detector /path/to/config.json
# or
python -m methyl_detector.cli /path/to/config.json
```

With options:

```bash
methyl-detector config.json --verbose --log-file output.log
methyl-detector --project project.json --per-cancer-group
```

---

## Quick Start

After completing either setup:

- **Docker:** Run via `docker exec -w /workspace/packages/methyldetector methylpipeline methyl-detector <config.json>` (paths in config must be valid inside the container).
- **Virtual environment:** Activate the project venv (`source .venv/bin/activate`), then run `methyl-detector <config.json>` (paths in config are on the host).

---

## Configuration

Minimal JSON config example:

```json
{
  "chromosome": "1",
  "contexts": ["CG"],
  "centroid1_dir": "/path/to/healthy/centroids",
  "centroid2_dir": "/path/to/cancer/centroids",
  "output_dir": "/path/to/output",
  "alpha": 0.05,
  "delta_mean_reduction": 0.1,
  "lambda_var": 2.0,
  "effect_size_coverage": 0.95,
  "validation_split_ratio": 0.2,
  "validation_n_repeats": 3
}
```

- **chromosome**: Single string (e.g. `"1"`) or list (e.g. `["1", "2", "X"]`) for multi-chromosome runs.  
- **contexts**: e.g. `["CG"]` or `["CG", "CHG", "CHH"]`.  
- **centroid1_dir** / **centroid2_dir**: Directories containing `{chrom}-{context}.h5` files (e.g. from MethylCentroid).  
- **output_dir**: Where to write DMP CSVs, summary, and classifier outputs.  
- **alpha**: FDR threshold (e.g. 0.01 or 0.05).  
- **effect_size_coverage**: Per-context cumulative effect-mass threshold for biological selection.  
- **validation_split_ratio / validation_n_repeats**: Repeated stratified held-out validation for BA reporting and top-k selection. Both are fields on **MethylDetectorConfig** (so project JSON values are applied). **results-\*.json** includes **`validation_row_split_semantics`**: `no_row_holdout` when the ratio is 0 (same rows used for calibration and test), or `stratified_holdout` when a fraction is held out per class. When repeats run, **`optimization_validation`** reports metrics on the **held-out test fold** (generalization), and **`training_fold_validation`** reports metrics on the **calibration / training fold** (in-sample predictive fit on the rows used for Platt fitting when applicable). With `validation_split_ratio=0`, both blocks describe the same cohort rows.  
- **GPU**: Not a config field — the detector uses GPU automatically when CuPy/CUDA is available and falls back to CPU otherwise.

Optional **filter funnel exploration** (`filter_funnel_explore`): sweep `effect_size_coverage` over a min/max/step range in one run and write `filter_funnel-{chromosome}.csv` per chromosome (columns: `n_statistical_dmps`, `effect_size_coverage`, `n_biological_dmps`). After all chromosomes finish, a combined `filter_funnel.csv` is written with a leading `chromosome` column aggregating every per-chrom file present.

For all parameters (biological filters, validation, etc.), see [MethylDetector_Comprehensive_Documentation.md](MethylDetector_Comprehensive_Documentation.md) and the config schema in the package.

---

## Python API

```python
from methyl_detector.models.config import MethylDetectorConfig
from methyl_detector.core.methyldetector import MethylDetector
from methyl_detector.utils.file_utils import load_config_from_json

# Load configuration
config = load_config_from_json("config.json")
# or build MethylDetectorConfig directly (MethylDetector config)

detector = MethylDetector(config)
result = detector.run()

# Single chromosome: result is MethylDetectorResult (MethylDetector result)
# Multiple chromosomes: result is list of MethylDetectorResult
if isinstance(result, list):
    for r in result:
        print(f"DMPs: {r.total_biological_dmps:,}")
else:
    print(f"DMPs: {result.total_biological_dmps:,}")
```

---

## Outputs

Typical outputs under `output_dir`:

- **Dual export** (`dmp_export_mode=dual`, default): `dmps-{chrom}-discovery.csv` (broad list for exploration), `dmps-{chrom}-selected.csv` (primary FeatureCuts / prediction panel), optional legacy `dmps-{chrom}-classifier-extended.csv`, `dmp-export-{chrom}.meta.json` (branch metadata), and `classifier-{chrom}-{contexts}.pkl`.
- **Unified export** (`dmp_export_mode=unified`): single `dmps-{chrom}.csv` aligned with the selected panel (optionally widened to `min_dmps_for_export` rows for mapping).
- **Summary / JSON**: `results-{chrom}.json` and optional validation blocks.
- **Classifier**: ECDF pickle as above.

Exact names depend on chromosome list, contexts, and per-cancer-group layout. See [IMPLEMENTATION.md](IMPLEMENTATION.md) and the theory chapter on MethylDetector.

---

## Troubleshooting

- **GPU not used:** Ensure CuPy is installed and `CUDA_VISIBLE_DEVICES` / the driver exposes a GPU to the process; otherwise the detector runs on CPU.
- **Paths in Docker:** Config paths must be valid inside the container. Use `/workspace/...` if the repo is mounted at `/workspace`.
- **Missing centroid files:** Ensure `centroid1_dir` and `centroid2_dir` contain the expected `{chrom}-{context}.h5` files (e.g. from MethylCentroid) for the chromosome(s) and contexts in your config.
- **MethylUtils not found:** Install MethylUtils first (`pip install -e .` in `packages/methylutils`), then install MethylDetector.

For theory and implementation details, see [THEORY.md](THEORY.md), [IMPLEMENTATION.md](IMPLEMENTATION.md), and the canonical theory book at [../../../docs/theory/README.md](../../../docs/theory/README.md).
