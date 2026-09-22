# MethylDetector Configuration Examples

This directory contains example configuration files for different MethylDetector analysis scenarios.

## Configuration Files

### 1. `config_example.json` - Basic Example
Basic configuration template with all required parameters for single comparison.

### 2. `config_single_comparison.json` - Single Comparison
Configuration for comparing two specific centroids (e.g., WT vs msh1 for chromosome 1, CG context).

**Usage:**
```bash
python -m methyl_detector examples/config_single_comparison.json
# Or with verbose output:
python -m methyl_detector examples/config_single_comparison.json --verbose
```

### 3. `config_multiple_chromosomes.json` - Multiple Comparisons
Configuration for running comparisons across multiple chromosomes and contexts.

**Usage:**
```bash
python -m methyl_detector examples/config_multiple_chromosomes.json
# Or with verbose output:
python -m methyl_detector examples/config_multiple_chromosomes.json --verbose
```

This will run comparisons for:
- Chromosomes: 1, 2, 3, 4, 5
- Contexts: CG, CHG, CHH
- Total: 15 comparisons (5 chromosomes × 3 contexts)

### 4. `config_strict_significance.json` - Conservative Analysis
Configuration with stricter significance parameters:
- Lower alpha (0.01 instead of 0.05)
- Higher minimum sample size (20 instead of 10)
- Stricter global significance threshold

### 5. `config_no_fdr.json` - No FDR Correction
Configuration without FDR correction for comparison with raw p-values.

## Configuration Parameters

### Single Comparison Config (uses `centroid1_path` and `centroid2_path`)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `centroid1_path` | string | Path to first centroid H5 file | Required |
| `centroid2_path` | string | Path to second centroid H5 file | Required |
| `output_dir` | string | Output directory for results | Required |
| `alpha` | float | Significance level for individual tests | 0.05 |
| `min_N` | int | Minimum sample size for reliable p-value calculation | 10 |
| `apply_fdr_correction` | bool | Whether to apply FDR correction | true |
| `fdr_method` | string | Multiple testing correction method | "bh" |
| `pvalue_aggregation_method` | string | P-value aggregation method | null |
| | | Available: "fisher", "stouffer", "lancaster", "tippett", "edgington", "mudholkar_george", "simes" | |
| `global_significance_threshold` | float | Threshold for global significance | 0.05 |

### Multiple Comparison Config (uses `centroid1_dir` and `centroid2_dir`)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `centroid1_dir` | string | Directory containing centroid1 files | Required |
| `centroid2_dir` | string | Directory containing centroid2 files | Required |
| `chromosomes` | array | List of chromosomes to process | ["1", "2", "3", "4", "5"] |
| `contexts` | array | List of contexts to process | ["CG", "CHG", "CHH"] |
| `output_dir` | string | Output directory for results | Required |
| `alpha` | float | Significance level for individual tests | 0.05 |
| `min_N` | int | Minimum sample size for reliable p-value calculation | 10 |
| `apply_fdr_correction` | bool | Whether to apply FDR correction | true |
| `fdr_method` | string | Multiple testing correction method | "bh" |
| `pvalue_aggregation_method` | string | P-value aggregation method | null |
| | | Available: "fisher", "stouffer", "lancaster", "tippett", "edgington", "mudholkar_george", "simes" | |
| `global_significance_threshold` | float | Threshold for global significance | 0.05 |

## File Naming Convention

For multiple comparisons, the tool expects centroid files to follow this naming pattern:
- `{chromosome}-{context}.h5`
- Example: `1-CG.h5`, `2-CHG.h5`, `3-CHH.h5`

## Output Structure

### Single Comparison
- `{prefix}_significant_positions.csv` - Significant positions with p-values and q-values
- `{prefix}_summary.txt` - Summary statistics
- `{prefix}_pi0_vs_lambda.html` - FDR analysis plot
- `{prefix}_significant_regions.csv` - Grouped significant regions

### Multiple Comparisons
- `methyl_detector_summary_report.txt` (or project-specific summary) - Overall summary
- `all_comparisons_summary.csv` - Summary table for all comparisons
- Individual comparison files in subdirectories
## Tips

1. **GPU**: MethylDetector uses GPU automatically when CuPy/CUDA is available (no detector config toggle).
2. **Sample Size**: Increase `min_N` for more reliable results but fewer positions analyzed
3. **FDR Correction**: Use `apply_fdr_correction: true` for multiple testing correction
4. **Significance Levels**: Adjust `alpha` and `global_significance_threshold` based on your research needs
5. **Config Detection**: The tool automatically detects whether to use single or multiple comparison mode based on the presence of `centroid1_path` vs `centroid1_dir` in the config file
