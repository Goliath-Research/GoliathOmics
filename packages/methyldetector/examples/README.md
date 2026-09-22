# MethylDetector Configuration Examples

This directory contains example JSON configuration files for different MethylDetector analysis scenarios.

## Configuration Files

### Single Comparison Examples

#### `config_example.json` - Basic Example
Basic configuration template with all required parameters for single comparison analysis.

#### `config_single_comparison.json` - Single Comparison
Configuration for comparing two specific centroids (e.g., WT vs msh1 for chromosome 1, CG context).

**Usage:**
```bash
python -m methyl_detector examples/config_single_comparison.json
# Or with verbose output:
python -m methyl_detector examples/config_single_comparison.json --verbose
```

#### `config_strict_significance.json` - Conservative Analysis
Configuration with stricter significance parameters:
- Lower alpha (0.01 instead of 0.05)
- Higher minimum sample size (20 instead of 10)
- Stricter global significance threshold

#### `config_no_fdr.json` - No FDR Correction
Configuration without FDR correction for comparison with raw p-values.

### Multiple Comparison Examples

#### `config_WT-msh1.json` - Arabidopsis WT vs msh1
Configuration for running comparisons across multiple chromosomes and contexts in Arabidopsis.

**Usage:**
```bash
python -m methyl_detector examples/config_WT-msh1.json
# Or with verbose output:
python -m methyl_detector examples/config_WT-msh1.json --verbose
```

#### `config_multiple_chromosomes.json` - Multiple Comparisons
Configuration for running comparisons across multiple chromosomes and contexts.

This will run comparisons for:
- Chromosomes: 1, 2, 3, 4, 5
- Contexts: CG, CHG, CHH
- Total: 15 comparisons (5 chromosomes × 3 contexts)

## Usage Instructions

1. **Replace file paths**: Update the `centroid1_path`, `centroid2_path`, `centroid1_dir`, and `centroid2_dir` fields with your actual data file paths.

2. **Adjust output directory**: Modify the `output_dir` field to point to your desired output location.

3. **Customize parameters**: Adjust alpha levels, minimum sample sizes, and other parameters based on your analysis requirements.

4. **Run the analysis**:
   ```bash
   python -m methyl_detector examples/your_config.json --verbose
   ```

## Configuration Parameters

### Single Comparison Parameters
- `centroid1_path`: Path to first centroid H5 file (required)
- `centroid2_path`: Path to second centroid H5 file (required)
- `output_dir`: Output directory for results (required)
- `alpha`: Significance level for individual tests (default: 0.05)
- `min_N_pct`: Minimum fraction of samples covering a position (default: 0.10)
- `min_N_abs`: Absolute minimum sample count (optional, default: None)
- GPU is used automatically when CuPy/CUDA is available (not a JSON config field).
### Multiple Comparison Parameters
- `centroid1_dir`: Directory containing centroid1 files (required)
- `centroid2_dir`: Directory containing centroid2 files (required)
- `chromosomes`: List of chromosomes to process (default: ["1", "2", "3", "4", "5"])
- `contexts`: List of contexts to process (default: ["CG", "CHG", "CHH"])
- Plus all single comparison parameters except the centroid paths

## File Naming Convention

For multiple comparisons, the tool expects centroid files to follow this naming pattern:
- `{chromosome}-{context}.h5`
- Example: `1-CG.h5`, `2-CHG.h5`, `3-CHH.h5`

## Tips

1. **GPU**: Install CuPy for your CUDA version; the detector selects GPU when available.
2. **Sample Size**: Increase `min_N_pct` for more reliable results but fewer positions analyzed
3. **Significance Levels**: Adjust `alpha` based on your research needs
4. **Config Detection**: The tool automatically detects whether to use single or multiple comparison mode based on the presence of `centroid1_path` vs `centroid1_dir` in the config file
