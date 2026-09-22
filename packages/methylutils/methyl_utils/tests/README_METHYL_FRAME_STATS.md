# MethylFrame Statistics Test Suite - Usage Guide

This test suite provides tools to compute statistics and generate histograms for `MethylSample` and `MethylCentroid` (single centroid type).

## Quick Start

### Option 1: Run with Mock Data in Container (Easiest - No Input Files Needed)

```bash
# Use -w flag to set working directory (recommended)
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py --mock --use-gpu --output-dir /tmp/results
```

This generates mock samples and creates histograms automatically with GPU acceleration.

### Option 1b: Run Locally (if not in container)

```bash
cd /home/ubuntu/MethylPipeline/packages/methylutils/methyl_utils/tests
python test_methyl_frame_statistics.py --mock --output-dir results
```

### Option 2: Run with Real Data from CSV File

1. Create a CSV file with sample folder names (one per line, no header):
   ```csv
   sample_folder_1
   sample_folder_2
   sample_folder_3
   ```

2. Run the test (in container):
   ```bash
   docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
     methylpipeline python3 test_methyl_frame_statistics.py \
       --csv sample_list.csv \
       --input-dir /path/to/samples/base/directory \
       --output-dir /tmp/results \
       --use-gpu
   ```

### Option 3: Run with Real Data from config.json

1. Create a config.json file:
   ```json
   {
     "samples": [
       "/path/to/sample1",
       "/path/to/sample2",
       "/path/to/sample3"
     ]
   }
   ```

2. Run the test (in container):
   ```bash
   docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
     methylpipeline python3 test_methyl_frame_statistics.py \
       --config config.json \
       --output-dir /tmp/results \
       --use-gpu
   ```

## Input File Formats

### CSV File Format

The CSV file should contain a single column with sample folder names (no header):

```
sample_folder_1
sample_folder_2
sample_folder_3
```

Each folder should contain H5 files named `{chrom}-{context}.h5`, for example:
- `1-CG.h5`
- `1-CHG.h5`
- `2-CG.h5`
- etc.

### config.json Format

```json
{
  "samples": [
    "/absolute/path/to/sample/directory1",
    "/absolute/path/to/sample/directory2",
    "/absolute/path/to/sample/file.h5"
  ]
}
```

Each path can be:
- A directory containing `{chrom}-{context}.h5` files
- A direct path to an `.h5` file

## Command-Line Options

```
Required (one of):
  --csv PATH          Path to CSV file with sample folder names
  --config PATH       Path to config.json file
  --mock              Generate mock data for testing

Options:
  --input-dir PATH    Base directory for samples (required with --csv)
  --output-dir PATH   Directory for histogram outputs (default: output)
  --chromosomes LIST  Chromosomes to process (e.g., 1 2 X)
  --contexts LIST     Contexts to process (default: CG CHG CHH)
  --stats-output PATH Save statistics summary to CSV/JSON file
```

## Examples

### Example 1: Test with Mock Data (All Chromosomes, CG Only)

```bash
python test_methyl_frame_statistics.py \
  --mock \
  --chromosomes 1 2 X \
  --contexts CG \
  --output-dir mock_results \
  --stats-output mock_stats.json
```

### Example 2: Real Data from CSV (Specific Chromosomes)

```bash
python test_methyl_frame_statistics.py \
  --csv my_samples.csv \
  --input-dir /data/samples \
  --chromosomes 1 2 3 \
  --contexts CG CHG \
  --output-dir results \
  --stats-output statistics.csv
```

### Example 3: Real Data from config.json

```bash
python test_methyl_frame_statistics.py \
  --config my_config.json \
  --output-dir results \
  --stats-output summary.json
```

## Running Pytest Tests

You can also run the tests using pytest:

```bash
# Run all tests (in container)
docker exec -w /workspace \
  methylpipeline pytest packages/methylutils/methyl_utils/tests/test_methyl_frame_statistics.py -v

# Run specific test (in container)
docker exec -w /workspace \
  methylpipeline pytest packages/methylutils/methyl_utils/tests/test_methyl_frame_statistics.py::test_mock_sample_creation -v

# Run with output
pytest methylutils/methyl_utils/tests/test_methyl_frame_statistics.py -v -s
```

## Output

The test suite generates:

1. **Statistics Summary**: Printed to console and optionally saved to CSV/JSON
   - Average mC, uC, coverage, methylation level
   - Total counts
   - Position counts
   - Centroid-specific stats (N, Sx, Sx2, etc.)

2. **Histogram HTML Files**: Interactive Plotly visualizations
   - `{sample_name}_mC_histogram.html` - Histogram of methylated counts
   - `{sample_name}_uC_histogram.html` - Histogram of unmethylated counts
   - `{sample_name}_coverage_histogram.html` - Histogram of coverage
   - `{sample_name}_methylation_level_histogram.html` - Histogram of methylation levels

## Using Programmatically

You can also use the functions directly in Python:

```python
from pathlib import Path
from methyl_utils.tests.methyl_frame_stats import (
    load_samples_from_csv,
    load_samples_from_config,
    compute_sample_statistics,
    generate_all_histograms
)

# Load samples
samples = load_samples_from_csv(
    csv_path=Path("sample_list.csv"),
    input_dir=Path("/path/to/samples"),
    chromosomes=['1', '2'],
    contexts=['CG']
)

# Compute statistics for each sample
for sample in samples:
    stats = compute_sample_statistics(sample)
    print(f"Sample: {stats['sample_name']}")
    print(f"  Positions: {stats['position_count']}")
    print(f"  Avg Coverage: {stats['avg_coverage']:.2f}")
    print(f"  Avg Methylation Level: {stats['avg_methylation_level']:.4f}")

# Generate histograms
output_dir = Path("histograms")
for i, sample in enumerate(samples):
    generate_all_histograms(sample, output_dir, f"sample_{i}")
```

## Testing Centroids

The test suite includes tests for `MethylCentroid`. 
These tests create centroids from multiple samples and verify:
- Statistics computation (including N, Sx, Sx2, etc.)
- Histogram generation
- Centroid-specific properties

Run centroid tests:
```bash
pytest methylutils/methyl_utils/tests/test_methyl_frame_statistics.py::test_methyl_centroid_statistics -v
pytest methylutils/methyl_utils/tests/test_methyl_frame_statistics.py::test_methyl_centroid_statistics -v
```

## Troubleshooting

### "No samples loaded!"
- Check that your CSV/config file paths are correct
- Verify that sample directories contain `{chrom}-{context}.h5` files
- Check that chromosome/context names match your files

### "ModuleNotFoundError: No module named 'methyl_utils'"
- Make sure you're running from within the container
- Verify the package is installed: `pip list | grep methyl`

### Histograms not generating
- Check that plotly is installed: `pip list | grep plotly`
- Verify output directory is writable

## File Structure

```
methylutils/methyl_utils/tests/
├── __init__.py
├── methyl_frame_stats.py          # Helper functions
├── test_methyl_frame_statistics.py # Main test file
└── README_METHYL_FRAME_STATS.md   # This file
```

