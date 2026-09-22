# MethylDetector Quick Start Guide

This guide will help you get MethylDetector up and running quickly. For Docker vs virtual environment setup, see [docs/USAGE.md](docs/USAGE.md).

## Prerequisites

- Docker and Docker Compose (if using container)
- NVIDIA GPU with CUDA support (optional, but recommended)
- Python 3.10+ (if running directly)

## Quick Start

### 1. Basic Configuration

Create a JSON configuration file with only the essential parameters:

```json
{
  "chromosome": "1",
  "contexts": ["CG"],
  "centroid1_dir": "/path/to/healthy/centroids",
  "centroid2_dir": "/path/to/cancer/centroids",
  "output_dir": "/path/to/output",
  "alpha": 0.01,
  "delta_mean_reduction": 0.2,
  "effect_size_coverage": 0.95,
  "ecdf_grid_size": 256
}
```


### 2. Run Analysis

```bash
source .venv/bin/activate

# From command line
methyl-detector --config config.json

# With verbose output
methyl-detector --config config.json --verbose

# With log file
methyl-detector --config config.json --log-file output.log
```

### 3. Check Results

After completion, check your output directory:

```
output/
├── dmps-1-biological-sorted.csv  # Biological DMPs sorted by importance
└── [additional chromosomes follow same pattern]
```

## Multi-Chromosome Processing

Process multiple chromosomes in a single run:

```json
{
  "chromosome": ["1", "2", "3", "X"],
  "contexts": ["CG"],
  "centroid1_dir": "/path/to/healthy/centroids",
  "centroid2_dir": "/path/to/cancer/centroids",
  "output_dir": "/path/to/output"
}
```

Each chromosome will be processed independently and generate separate output files.

## Multi-Context Analysis

Process multiple methylation contexts (CG, CHG, CHH) together:

```json
{
  "chromosome": "1",
  "contexts": ["CG", "CHG", "CHH"],
  "centroid1_dir": "/path/to/healthy/centroids",
  "centroid2_dir": "/path/to/cancer/centroids",
  "output_dir": "/path/to/output",
  "use_context_weights": true
}
```

Contexts will be automatically weighted based on their biological importance.

## Using Python API

```python
from methyl_detector import MethylDetector
from methyl_detector.models.config import MethylDetectorConfig
from methyl_detector.core.methyldetector import MethylDetector
from methyl_detector.utils.file_utils import load_config_from_json

# Load configuration
config = load_config_from_json("config.json")

# Initialize and run
detector = MethylDetector(config)
result = detector.run()

# Handle results (single or multiple chromosomes)
if isinstance(result, list):
    print(f"Processed {len(result)} chromosomes")
    for i, r in enumerate(result):
        print(f"Chromosome {i+1}: {r.total_biological_dmps:,} DMPs")
else:
    print(f"Found {result.total_biological_dmps:,} DMPs")
```

## Configuration Examples

### Minimal Configuration

```json
{
  "chromosome": "1",
  "contexts": ["CG"],
  "centroid1_dir": "/centroids/healthy",
  "centroid2_dir": "/centroids/cancer",
  "output_dir": "/output"
}
```

### With FeatureCuts panel selection (validation BA)

```json
{
  "chromosome": "1",
  "contexts": ["CG"],
  "centroid1_dir": "/centroids/healthy",
  "centroid2_dir": "/centroids/cancer",
  "output_dir": "/output",
  "classifier_dmp_selection": "featurecuts_validation",
  "target_balanced_accuracy": 0.95,
  "validation_split_ratio": 0.2
}
```

### With Real Validation Samples

```json
{
  "chromosome": "1",
  "contexts": ["CG"],
  "centroid1_dir": "/centroids/healthy",
  "centroid2_dir": "/centroids/cancer",
  "output_dir": "/output",
  "validation_split_ratio": 0.2,
  "centroid1_validation_samples": [
    "/samples/healthy1",
    "/samples/healthy2"
  ],
  "centroid2_validation_samples": [
    "/samples/cancer1",
    "/samples/cancer2"
  ]
}
```

### Using Metadata for Validation

```json
{
  "chromosome": "1",
  "contexts": ["CG"],
  "centroid1_dir": "/centroids/healthy",
  "centroid2_dir": "/centroids/cancer",
  "output_dir": "/output",
  "centroid1_validation_samples": "use_metadata",
  "centroid2_validation_samples": "use_metadata"
}
```

This will read sample paths from the centroid HDF5 file metadata.

### How to provide methylation samples for real validation

To validate Balanced Accuracy with **real data for both groups**:

1. **Provide samples in one of two ways:**

   - **Explicit paths (recommended when you have a dedicated validation set):**  
     Set `centroid1_validation_samples` and `centroid2_validation_samples` to **arrays of paths**.  
     Each path is either:
     - A **directory** containing per-chromosome, per-context H5 files: **`{chromosome}-{context}.h5`** (e.g. for chromosome 1 and context CG the file must be **`1-CG.h5`** inside that directory).  
       Example: `/data/healthy/sample_01` with `1-CG.h5`, `2-CG.h5`, … inside.  
       If extraction loads 0 samples, check the log for “Example expected path” and ensure that exact file exists (same naming as MethylCentroid output).
     - Or a **single `.h5` file** path.
     You must have **at least one sample for each group**; more samples give a more reliable BA and split (e.g. ~20% test).

   - **Use centroid metadata:**  
     Set both to `"use_metadata"`.  
     The pipeline reads sample paths from the centroid H5 metadata (`samples_used` or `sample_paths`).  
     Use this when the same samples used to build the centroids are acceptable for validation (no separate holdout).

2. **Optional:**  
   - `validation_split_ratio`: fraction held out for test. Default `0` = no split (use all real validation samples for BA). Set e.g. `0.2` for a holdout when you want train/test separation.  
   - `validation_min_coverage`: min coverage when extracting methylation from these samples (default `4`; use lower than centroid `min_coverage` if needed).

If no real samples are found (or only one group has samples), FeatureCuts / validation BA steps are skipped and the detector logs a warning (elbow-based panel selection still runs when configured).

## Data Format

### Centroid Files

Centroid files must be in HDF5 format with naming:
- Format: `{chromosome}-{context}.h5`
- Example: `1-CG.h5`, `1-CHG.h5`, `X-CG.h5`

Files should be in the directories specified by `centroid1_dir` and `centroid2_dir`.

### Validation Samples

Validation samples can be:
- Directory paths containing `.h5` files with format `{chromosome}-{context}.h5`
- Or `"use_metadata"` to read from centroid file metadata

## Output Files

### Per Chromosome Outputs

- **`dmps-{chromosome}-biological-sorted.csv`**: Biological DMPs sorted by importance

### CSV Columns

Each DMP CSV includes:
- Position information: `chromosome`, `context`, `position`
- Statistics: `p_value`, `q_value`, `delta_mean`, `delta_sign`
- Biological metrics: `overlap`, `effect_size`, `context_weight`
- Mean methylation: `mean1`, `mean2`

## Troubleshooting

### No DMPs Found

If you get 0 biological DMPs, try relaxing filters:

```json
{
  "alpha": 0.05,
  "delta_mean_reduction": 0.1,
  "effect_size_coverage": 0.99
}
```

### GPU Out of Memory

Reduce chromosome batch size, lower `ecdf_grid_size`, or run with `CUDA_VISIBLE_DEVICES` unset so the process uses CPU (CuPy not required for CPU path).

### Validation Errors

Ensure validation samples exist and are accessible:

```bash
# Check sample directories exist
ls /path/to/validation/samples/

# Check H5 files are present
ls /path/to/validation/samples/*/1-CG.h5
```

### Configuration Validation Errors

Common issues:
- `centroid1_dir` and `centroid2_dir` must exist and be directories
- `chromosome` must be valid (1-22, X, Y, M, MT)
- `contexts` must be valid (CG, CHG, CHH)
- File format: `{chromosome}-{context}.h5`

## Next Steps

1. **Explore Results**: Load and analyze the CSV files
2. **Use Classifier**: Load the `.pkl` file with MethylClassifier
3. **Multi-Chromosome**: Try processing multiple chromosomes
4. **Context Selection**: Experiment with different context combinations

See the [main README](README.md) for complete documentation.
