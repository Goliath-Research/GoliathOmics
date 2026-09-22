# Three-Stage DMP Export Feature

## Overview

MethylDetector exports **three separate CSV files** representing the three stages of DMP selection, allowing you to map each stage to genes and compare their biological interpretation.

### Current multi-context export (actual filenames)

The multi-context pipeline currently writes:

- **`dmps-{chromosome}-biological-sorted.csv`** — All DMPs passing the **biological filter** (q-value ≤ alpha, |delta_mean| ≥ min_delta_mean, overlap ≤ max_overlap, effect_size ≥ min_effect_size), sorted by importance. This is the full biologically significant set (Stage 1 equivalent).
- **`dmps-{chromosome}.csv`** — Final selected DMPs after optimization (smaller subset used by the classifier).

**MethylMapper** defaults to the pattern `dmps-*-biological-sorted.csv` so that gene mapping uses only these biologically filtered DMPs, not every CSV in the detection directory. Override via the mapper step config if you want to map the optimized subset or all CSVs.

## The Three Stages

### Stage 1: Biologically Significant DMPs
**File**: `dmps-{chromosome}-1-biological.csv`

This file contains **all DMPs that pass biological significance filters**:
- Statistical significance (q-value ≤ alpha)
- Minimum delta mean (|Δμ| ≥ min_delta_mean)
- Maximum overlap (ECDF-based; overlap ≤ max_bc)
- Computed effect size and biological importance

**Purpose**: Shows the complete set of biologically relevant DMPs before any optimization.

### Stage 2: Binary Search Selection
**File**: `dmps-{chromosome}-2-binary-search.csv`

This file contains **DMPs selected by binary search optimization**:
- Subset of Stage 1 DMPs ranked by biological importance
- Selected to achieve target Balanced Accuracy (default: 0.99)
- Uses real or synthetic validation samples for performance evaluation

**Purpose**: Shows the minimal DMP set that achieves the target classification accuracy.

### Stage 3: Differential Evolution Optimization
**File**: `dmps-{chromosome}-3-differential-evolution.csv`

This file contains **DMPs after Differential Evolution global optimization**:
- Fine-tuned selection from Stage 2
- Optimizes the number of DMPs (k) to maximize Balanced Accuracy
- Uses advanced global search to find the optimal subset size

**Purpose**: Shows the final optimized DMP set with best performance.

## When Are Files Generated?

The files are generated based on your configuration settings:

### Always Generated:
- **Stage 1** (`-1-biological.csv`): Always exported

### Conditionally Generated:
- **Stage 2** (`-2-binary-search.csv`): 
  - Generated when `export_all_biological_dmps = false`
  - Skipped when `export_all_biological_dmps = true`

- **Stage 3** (`-3-differential-evolution.csv`):
  - Generated when `optimize_for_validation_accuracy = true`
  - Requires binary search to be enabled (`export_all_biological_dmps = false`)

### Default CSV:
- **`dmps-{chromosome}.csv`**: 
  - Generated when DE optimization is disabled
  - Contains the final selected DMPs (same as Stage 2 in that case)

## Configuration Example

```json
{
  "chromosome": "1",
  "export_all_biological_dmps": false,
  "optimize_for_validation_accuracy": true,
  "target_balanced_accuracy": 0.99,
  "min_dmps_for_export": 5000
}
```

With this configuration, you will get:
- ✅ `dmps-1-1-biological.csv` (all biological DMPs)
- ✅ `dmps-1-2-binary-search.csv` (binary search selection)
- ✅ `dmps-1-3-differential-evolution.csv` (final optimized)

## CSV Structure

All three CSVs have the same column structure:

```csv
chromosome,context,position,p_value,q_value,delta_mean,delta_sign,overlap,effect_size,context_weight,mean1,mean2
```

### Key Columns:
- **chromosome**: Chromosome identifier
- **context**: Methylation context (CG, CHG, CHH)
- **position**: Genomic position
- **p_value**: Raw p-value from statistical test
- **q_value**: FDR-corrected q-value
- **delta_mean**: Mean methylation difference (Δμ)
- **delta_sign**: Direction of change (+1 or -1)
- **overlap**: ECDF-based overlap (∫ min(f1, f2) dx)
- **effect_size**: Computed biological effect size
- **context_weight**: Multi-context weight (for weighted classification)
- **mean1, mean2**: Mean methylation for each centroid

## Usage for Gene Mapping

### Example Workflow:

1. **Map each CSV to genes** using your preferred annotation tool (e.g., HOMER, ChIPseeker):
   ```bash
   # Map Stage 1: All biological DMPs
   annotatePeaks.pl dmps-1-1-biological.csv hg38 > genes-1-biological.txt
   
   # Map Stage 2: Binary search selection
   annotatePeaks.pl dmps-1-2-binary-search.csv hg38 > genes-2-binary-search.txt
   
   # Map Stage 3: DE optimization
   annotatePeaks.pl dmps-1-3-differential-evolution.csv hg38 > genes-3-differential-evolution.txt
   ```

2. **Compare gene lists**:
   - Genes in Stage 1 but not Stage 2: Biological signal but not needed for classification
   - Genes in Stage 2 but not Stage 3: Important for BA target, refined by DE
   - Genes in Stage 3: Final optimized set with best performance

3. **Biological Interpretation**:
   - **Stage 1**: Comprehensive view of all affected pathways
   - **Stage 2**: Core discriminatory genes (minimal set for classification)
   - **Stage 3**: Optimized discriminatory genes (best performance)

## Logging Output

During execution, you'll see clear logging for each stage:

```
🔬 Filtering biologically significant DMPs...
✅ Biological DMPs: 125,432 (retention: 15.3%)
💾 Exporting Stage 1: Biological DMPs...
📁 Exported 125,432 DMPs to dmps-1-1-biological.csv

🎯 Running binary search to optimize DMP selection...
✅ Selected 8,234 DMPs out of 125,432 biological DMPs
💾 Exporting Stage 2: Binary Search DMPs...
📁 Exported 8,234 DMPs to dmps-1-2-binary-search.csv

🧬 Starting Differential Evolution optimization from k=8,234...
📈 DE optimization: k=8,234 → k=7,156
💾 Exporting Stage 3: Differential Evolution Optimized DMPs...
📁 Exported 7,156 DMPs to dmps-1-3-differential-evolution.csv
```

## Technical Details

### Binary Search Algorithm:
- **Objective**: Find minimum k DMPs achieving target Balanced Accuracy
- **Method**: Binary search on sorted DMPs (by biological importance)
- **Metric**: Balanced Accuracy = (Sensitivity + Specificity) / 2
- **Constraint**: Exports at least `min_dmps_for_export` DMPs

### Differential Evolution Algorithm:
- **Objective**: Maximize Balanced Accuracy by fine-tuning k
- **Method**: Global optimization using scipy.optimize.differential_evolution
- **Parameters**: maxiter=30, popsize=10, strategy='best1bin'
- **Search Space**: k ∈ [10, max_biological_dmps]

## Benefits for Analysis

1. **Comprehensive View**: Stage 1 shows all biological signal
2. **Efficiency Analysis**: Compare Stage 1→2 to see which DMPs are redundant
3. **Optimization Impact**: Compare Stage 2→3 to see DE refinement
4. **Gene Pathway Analysis**: Map each stage to discover pathway differences
5. **Reproducibility**: Track exact DMP selection at each optimization step

## Example Analysis Questions

- Which genes are lost between Stage 1 and Stage 2?
  → Biologically significant but redundant for classification

- Which genes are lost between Stage 2 and Stage 3?
  → DE determined these don't contribute to optimal accuracy

- Which pathways are enriched in Stage 3 vs Stage 1?
  → Core discriminatory pathways vs all affected pathways

- What is the overlap between stages?
  → Use Venn diagrams to visualize DMP/gene selection refinement

