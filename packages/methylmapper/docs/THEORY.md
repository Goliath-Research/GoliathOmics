# MethylMapper Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/07-methylmapper.md`](../../../docs/theory/chapters/07-methylmapper.md).

## Scope

`methylmapper` maps locus-level DMPs to genes and genomic features, then aggregates evidence at gene level. The package combines:

- deterministic interval mapping,
- optional Azure SQL stored-procedure-backed mapping,
- weighted Stouffer-style gene-level p-value aggregation,
- heuristic biological importance ranking,
- external-service-backed disease evidence enrichment.

## Method Status

- **Principled**: signed weighted Stouffer aggregation, followed by q-value adjustment.
- **Approximate**: gene-level aggregation assumes weaker dependence than real neighboring DMPs usually exhibit.
- **Heuristic**: biological importance scores, DMP-count stability heuristics, feature weights.
- **External-service-backed**: Azure SQL stored procedures, DisGeNET, Open Targets, optional Grok-assisted evidence synthesis.

## Key Code Paths

- `methyl_mapper/bedtools_mapper.py`
- `methyl_mapper/mapper.py`
- `methyl_mapper/gene_disease_enricher.py`
- `methyl_mapper/config.py`
