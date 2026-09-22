# MethylEnricher Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/08-methylenricher.md`](../../../docs/theory/chapters/08-methylenricher.md).

## Scope

`methylenricher` is a downstream interpretation package. It combines:

- external over-representation analysis through Enrichr,
- pathway graph construction from gene-set overlap,
- graph clustering for pathway modules,
- heuristic module scoring and disease-prior ranking.

## Method Status

- **External-service-backed**: Enrichr supplies the enrichment statistics.
- **Principled**: graph clustering uses standard methods such as Louvain over pathway-overlap graphs.
- **Heuristic**: module enrichment scores, disease relevance tiers, and theme labeling rules.

## Key Code Paths

- `methyl_enricher/enricher.py`
- `methyl_enricher/pathway_graph.py`
- `methyl_enricher/module_scorer.py`
- `methyl_enricher/module_pipeline.py`
- `methyl_enricher/pathway_normalizer.py`
