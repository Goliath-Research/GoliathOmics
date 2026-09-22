# MethylMapper Implementation Notes

## Canonical Theory

For formulas, assumptions, and caveats, see [`docs/theory/chapters/07-methylmapper.md`](../../../docs/theory/chapters/07-methylmapper.md).

## Main Code Paths

- `methyl_mapper/bedtools_mapper.py`: **canonical** interval mapping (default: all GTF feature types), per-gene aggregation with exclusive per-(DMP,gene) feature-hit summaries (`hits_*`), optional auxiliary BED overlaps and `bedtools closest` to nearest gene, biological importance scoring.
- `methyl_mapper/mapper.py`: Azure SQL upload and stored-procedure orchestration (legacy / comparison).
- `methyl_mapper/gene_disease_enricher.py`: Grok (synchronous annotation by default) + Open Targets (evidence and scores); DisGeNET only if explicitly enabled; caching and threshold profiles.
- `methyl_mapper/project_resolver.py`: project-config integration.
- `methyl_mapper/secure_credentials.py`: local encrypted cache and optional cloud secret resolution.

## Implementation Notes

- **Bedtools** is the primary mapping path for new WGBS work; Azure SQL SP remains available for backfill or parity checks.
- By default the GTF intersect keeps **every** `feature` type; restrict with `feature_types` / `--feature-types`.
- Gene-level aggregation emits a slimmed schema for downstream enrichment (`gene_name`, `gene_id`, `unique_dmps`, `gene_importance`, `gene_effect_signed_wsum`, `gene_direction`, `gene_effect_abs_wsum`, `gene_support_n`, `gene_effect_compound`, `feature_importance_*`, `feature_direction_*`, `feature_effect_signed_wsum_*`, `hits_*`, `gene_p_value`, `gene_q_value`, disease subset, links). Feature hits use **exclusive** per-(DMP,gene) assignment with priority `promoter > exon > intron > gene_body > terminator`.
- Canonical biological importance uses a biology-weight matrix (feature × hyper/hypo) on detector `effect_size`: `w_i = frequency × bio_weight(feature, direction)`; `gene_importance = gene_effect_abs_wsum × gene_direction_coherence × √(gene_support_freq)`. See [`BIOLOGICAL_IMPORTANCE_AUDIT.md`](BIOLOGICAL_IMPORTANCE_AUDIT.md).
- Configure biology weights via profile `actionConfig.mapper.biology_weights` (`BiologyWeightConfig` defaults in `methyl_mapper/config.py`).
- Stability/fixed-panel inputs require strict `frequency` in `[0,1]` for biological importance aggregation.
- Disease enrichment: merged outputs use **Open Targets** for association evidence and scores; **Grok** supplies narrative annotation (`grok_annotation_summary`, `gene_basic_description`). Grok defaults to **single-threaded** synchronous `chat/completions` (batch size ≤ 20); xAI Batch API is opt-in.
- Optional **auxiliary BED** files (e.g. enhancers, ChIP) add per-DMP overlap columns; **`--closest-gene`** adds nearest gene body distance from the GTF.
- Disease enrichment is an annotation layer and may depend on network availability, credentials, and cache state.

## Additional Package Guides

- [`../BEDTOOLS_MAPPER_README.md`](../BEDTOOLS_MAPPER_README.md)
- [`../QUICK_START.md`](../QUICK_START.md)
- [`../INSTALLATION.md`](../INSTALLATION.md)
