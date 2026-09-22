# Biological importance — canonical specification

This document defines how MethylDetector `effect_size` propagates to gene- and feature-level biological importance in MethylMapper.

## 1. Atomic signal (DMP)

Per CpG position, MethylDetector exports:

```
effect_size = |delta_mean| × (1 − overlap) × exp(−λ·(√var1 + √var2)) × mean_level_weight
```

Statistical significance: ECDF KS → `p_value` / `q_value`. Biological filter: per-context `effect_size_coverage`.

## 2. Per-DMP weight in mapper (biology matrix)

Each deduped mapping row `i` (exclusive feature assignment: promoter > exon > intron > gene_body > terminator) uses:

```
w_i = frequency_i × bio_weight(feature_i, hyper|hypo)
```

`hyper` when `sign(delta_mean_i) > 0`, `hypo` when negative (fallback: `sign(effect_size_i)`).

Default biology matrix (`BiologyWeightConfig` in [`methyl_mapper/config.py`](../methyl_mapper/config.py)):

| Feature | hyper | hypo |
|---------|-------|------|
| promoter | 2.0 | 1.0 |
| exon | 1.5 | 1.0 |
| intron | 0.5 | 0.7 |
| gene_body | 1.0 | 1.0 |
| terminator | 0.5 | 0.5 |

Override via `actionConfig.mapper.biology_weights` in profile/site actionConfig.

`frequency` encodes stability/recurrence. `region_weight` from BED is **not** used in biological importance (only in Stouffer/combined_weight for gene p-values).

## 3. Gene-level aggregation

Over unique `(gene, dmp_name)`:

```
abs_term_i    = w_i × |effect_size_i|
signed_term_i = abs_term_i × sign_i

gene_effect_abs_wsum     = Σ abs_term_i
gene_effect_signed_wsum  = Σ signed_term_i
gene_direction           = sign(gene_effect_signed_wsum)
gene_direction_coherence = |Σ signed_term_i| / Σ abs_term_i
gene_support_n           = count unique DMPs
gene_support_freq        = mean(frequency_i)

gene_importance = gene_effect_abs_wsum × gene_direction_coherence × √(gene_support_freq)
```

**Primary gene rank:** `gene_importance`.

**Diagnostics (not for downstream sort/filter):** `gene_effect_compound`, `gene_effect_abs_wmean`, `gene_effect_size` (signed weighted mean).

## 4. Feature-level aggregation

Per `(gene, feature_bucket)` with dedup `(gene, feature, dmp)`:

```
feature_importance_{bucket} = Σ abs_term_i × coherence_bucket × √(mean frequency in bucket)
feature_direction_{bucket}  = sign(Σ signed_term_i in bucket)
feature_effect_signed_wsum_{bucket} = Σ signed_term_i in bucket
```

**Primary feature rank:** `feature_importance_{promoter,exon,intron,gene_body,terminator}` with `feature_direction_*` for hyper/hypo interpretation.

## 5. Export columns

**Ranking:** `gene_importance`, `feature_importance_*`

**Interpretation:** `gene_direction`, `gene_direction_coherence`, `gene_effect_abs_wsum`, `gene_effect_signed_wsum`, `gene_support_n`, `gene_support_freq`, `feature_direction_*`, `feature_effect_signed_wsum_*`, `hits_*`

**Statistics (orthogonal):** `gene_p_value`, `gene_q_value`, `min_p_value`, `min_q_value`

Removed legacy columns: `mean_effect_size`, `gene_score`, `gene_feature_importance`, `effect_size_{feature}`, `direction_balance_*`, `gene_feature_effect_compound`, row-level `biological_importance`.

## 6. Downstream contract

- **methyl-enricher:** sort/filter on `gene_importance`
- **methyl-gene-select:** ECDF weights from `gene_importance`
- **methyl-gene-feature-select:** rank from `all-gene_name-combined.csv` using `feature_importance_{feature_type}`
