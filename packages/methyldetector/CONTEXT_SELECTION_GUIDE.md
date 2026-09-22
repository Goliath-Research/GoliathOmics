# Methylation Context Selection Guide

## Overview

MethylDetector can analyze one or more methylation contexts (CG, CHG, CHH). The `contexts` parameter in the configuration file controls which contexts are included in the analysis.

## Context Parameter

```json
{
  "contexts": ["CG"]
}
```

### Valid Values

- **Single context**: `["CG"]`, `["CHG"]`, or `["CHH"]`
- **Multiple contexts**: `["CG", "CHG"]`, `["CG", "CHH"]`, `["CHG", "CHH"]`, or `["CG", "CHG", "CHH"]`

### Default Value

**`["CG"]`** - CG context provides the strongest signal for most biological analyses, particularly in mammalian systems.

## Biological Rationale

### CG Context (Recommended Default)

**Use CG alone when:**
- Analyzing mammalian methylation patterns
- Looking for the strongest, most specific signal
- Working with cancer biomarkers (CG methylation is most relevant)
- Designing clinical assays (CG context is standard)
- Maximum signal-to-noise ratio is needed

**Characteristics:**
- Most abundant and well-studied methylation in mammals
- Strong regulatory function (promoters, enhancers, gene bodies)
- Best annotated in databases (TCGA, ENCODE, etc.)
- Most stable and reproducible across replicates
- Targeted by clinical methylation assays (e.g., Illumina EPIC array)

### CHG and CHH Contexts

**Consider adding CHG/CHH when:**
- Studying plants (CHG/CHH methylation is abundant and functional)
- Investigating embryonic development or stem cells
- Analyzing transposable elements (TE silencing)
- Researching non-canonical methylation roles

**Important Notes:**
- In mammals, CHG/CHH methylation is much less abundant than CG
- Can dilute the signal when analyzing cancer vs healthy tissue
- May introduce noise if the biological question focuses on CG

## Practical Examples

### Example 1: Prostate Cancer Detection (CG Only - Recommended)

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

**Why CG only?**
- Cancer methylation changes primarily occur in CG context
- Provides strongest discriminatory signal
- Standard for clinical biomarker development
- Better performance than multi-context analysis

### Example 2: Plant Methylation Analysis (All Contexts)

```json
{
  "chromosome": "1",
  "contexts": ["CG", "CHG", "CHH"],
  "centroid1_dir": "/path/to/control/centroids",
  "centroid2_dir": "/path/to/treatment/centroids",
  "output_dir": "/path/to/output",
  "use_context_weights": true
}
```

**Why all contexts?**
- Plants have functional CHG and CHH methylation
- All contexts contribute to gene regulation
- Context weighting ensures proper integration

### Example 3: Transposable Element Analysis (CHG and CHH Focus)

```json
{
  "chromosome": "1",
  "contexts": ["CHG", "CHH"],
  "centroid1_dir": "/path/to/centroids1",
  "centroid2_dir": "/path/to/centroids2",
  "output_dir": "/path/to/output"
}
```

**Why CHG/CHH?**
- TEs are often silenced via non-CG methylation
- Specific to research question
- Removes CG context to focus on non-canonical methylation

## Impact on Results

### Signal Dilution Example (Prostate Cancer)

Based on actual analysis results:

**Using all contexts `["CG", "CHG", "CHH"]`:**
- Stage 1: 90,100 biological DMPs
- Stage 3: 962 optimized DMPs
- Context distribution: 78.2% CG, 8.5% CHG, 13.3% CHH
- Mean effect size: 0.87

**Using CG only `["CG"]`:**
- Expected: Higher number of CG-specific DMPs
- Expected: Stronger signal (higher mean effect size)
- Expected: Better classification accuracy
- Expected: More biologically relevant for cancer

### Multi-Context Weighting

When using multiple contexts, MethylDetector can apply context weighting:

```json
{
  "contexts": ["CG", "CHG", "CHH"],
  "use_context_weights": true,
  "trimmed_percentile_low": 0.10,
  "trimmed_percentile_high": 0.01
}
```

**How it works:**
- Computes effect size per context
- Calculates trimmed mean (removes outliers)
- Normalizes to sum=1.0
- Weights DMPs by context importance

**When to use:**
- Multi-context analysis where contexts have different importance
- Want to avoid CHG/CHH diluting CG signal
- Need automatic adjustment for context abundance

**When NOT to use:**
- Single context analysis (redundant)
- Want equal weighting across contexts
- Context contributions should be equal by design

## Performance Considerations

### Processing Time

Processing time scales with number of contexts:
- **CG only**: 1x baseline
- **CG + CHG**: ~2x baseline
- **CG + CHG + CHH**: ~3x baseline

### Memory Usage

Memory usage scales with total DMPs across all contexts:
- More contexts = more DMPs = more memory
- CG alone typically sufficient for most analyses

### File Size

Output files will be larger with multiple contexts:
- Each DMP has a context field
- More contexts = more total DMPs
- BED files will be larger

## Recommendations by Use Case

### Clinical Biomarker Development
```json
"contexts": ["CG"]
```
✅ Standard for clinical assays  
✅ Best signal-to-noise  
✅ Most reproducible  

### Academic Cancer Research
```json
"contexts": ["CG"]
```
✅ Focus on established mechanisms  
✅ Easier to compare with literature  
✅ Cleaner signal  

### Plant Biology
```json
"contexts": ["CG", "CHG", "CHH"]
"use_context_weights": true
```
✅ All contexts are functional  
✅ Complete methylation landscape  

### Epigenetic Mechanism Discovery
```json
"contexts": ["CG"]  # Start here
# Then try ["CHG"], ["CHH"] separately for comparison
```
✅ Separate analyses reveal context-specific effects  
✅ Better mechanistic understanding  

### Transposable Element Studies
```json
"contexts": ["CHG", "CHH"]
```
✅ TE silencing is often non-CG  
✅ Focuses on relevant mechanisms  

## Migration Guide

### Updating Existing Configs

If you have an existing config without the `contexts` parameter:

**Before (uses default ["CG", "CHG", "CHH"]):**
```json
{
  "chromosome": "1",
  "centroid1_dir": "/path/to/dir1",
  "centroid2_dir": "/path/to/dir2"
}
```

**After (explicit CG only):**
```json
{
  "chromosome": "1",
  "contexts": ["CG"],
  "centroid1_dir": "/path/to/dir1",
  "centroid2_dir": "/path/to/dir2"
}
```

### Default Change

⚠️ **Important:** The default has been changed from `["CG", "CHG", "CHH"]` to `["CG"]` based on practical experience showing that:
- CG provides stronger signal for most analyses
- CHG/CHH can dilute cancer biomarker signals
- Single-context analysis is simpler and faster

**If you want the old behavior**, explicitly specify:
```json
"contexts": ["CG", "CHG", "CHH"]
```

## Validation

The configuration will validate your contexts parameter:

```python
# Valid
"contexts": ["CG"]
"contexts": ["CG", "CHG"]
"contexts": ["CG", "CHG", "CHH"]

# Invalid - will raise error
"contexts": []  # At least one context required
"contexts": ["cg"]  # Case-sensitive, must be uppercase
"contexts": ["CpG"]  # Invalid name, use "CG"
"contexts": ["CG", "INVALID"]  # Invalid context name
```

## FAQ

**Q: Should I use one context or multiple?**  
A: For mammalian cancer studies, use `["CG"]`. For plant studies, use all three with weighting.

**Q: Will using multiple contexts improve my results?**  
A: Usually no for mammalian studies. CG alone typically gives better results as CHG/CHH can add noise.

**Q: How do I compare CG vs CHG vs CHH?**  
A: Run separate analyses with `["CG"]`, `["CHG"]`, and `["CHH"]` individually, then compare results.

**Q: Can I change contexts between runs?**  
A: Yes! Each run is independent. Just update the config and output directory.

**Q: What if my centroid files don't exist for all contexts?**  
A: MethylDetector will skip missing contexts with a warning. Only specify contexts you have data for.

**Q: Does context selection affect validation accuracy?**  
A: Yes! Cleaner signal (e.g., CG only) typically leads to better validation performance.

## Summary

**Default recommendation:** `"contexts": ["CG"]`

**Use this for:**
- ✅ Cancer biomarker discovery
- ✅ Clinical assay development  
- ✅ Mammalian epigenetics
- ✅ Most standard analyses

**Consider multiple contexts only for:**
- 🌱 Plant biology
- 🧬 Non-canonical methylation research
- 🔬 Comprehensive methylation landscape
- 📊 Context-specific mechanism discovery

**Remember:** More is not always better. Focus on the context(s) relevant to your biological question.

