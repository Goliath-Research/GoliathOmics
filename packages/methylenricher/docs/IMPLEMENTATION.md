# MethylEnricher Implementation Notes

## Canonical Theory

For formulas, assumptions, and caveats, see [`docs/theory/chapters/08-methylenricher.md`](../../../docs/theory/chapters/08-methylenricher.md).

## Main Code Paths

- `methyl_enricher/enricher.py`: gene-list loading, Enrichr calls, filtering.
- `methyl_enricher/pathway_graph.py`: pathway overlap graph and clustering.
- `methyl_enricher/module_scorer.py`: heuristic module scoring and disease relevance.
- `methyl_enricher/module_pipeline.py`: end-to-end orchestration.
- `methyl_enricher/ppi_network.py`: optional STRING/local-edge PPI refinement (graph build, metrics, communities, module coherence).
- `methyl_enricher/module_network_plot.py`: shared pathway network exporters (Plotly, PyVis, Cytoscape.js) and Cytoscape payload builder.
- `methyl_enricher/module_network_dash.py`: optional Dash + dash_cytoscape interactive viewer.
- `methyl_enricher/pathway_normalizer.py`: pathway theme normalization and labeling.
- `methyl_enricher/network_discovery.py`: post-run candidate-edge discovery, STRING novelty labeling, SQLite snapshot storage, and curated edge CSV export.
- `methyl_enricher/network_discovery_cli.py`: CLI entrypoint for discovery scans and export.

## Implementation Notes

- Statistical enrichment is delegated to Enrichr through `gseapy`.
- `--project` path resolution supports two execution layouts:
  - single-run resolution from `resolve_enricher_paths()`,
  - per-comparison runs from `resolve_enricher_paths_per_cancer_group()` when multiple groups exist and no explicit input/output overrides are passed.
- Module construction depends on graph thresholds and clustering settings.
- Several ranking and labeling steps are intentionally heuristic and should remain documented as such.
- Optional `network_refinement` adds a second graph layer from gene-level PPI edges (STRING API or local edge CSV) and writes:
  - `ppi_network_edges.csv`
  - `ppi_node_metrics.csv` (centrality plus methylation-weighted `combined_hub_score` by default)
  - `ppi_hubs.csv`
  - `ppi_module_coherence.csv` (includes `ppi_mean_combined_hub_score` when signal-weighted ranking is active)
- Module ranking remains backward compatible; `modules_ranked.csv` preserves historical columns and now also reports `Base_score`, `PPI_coherence_score`, and `Blended_score`.
- PPI refinement is best interpreted as structural support for module quality, not as a replacement for disease evidence sources.
- For `source=string_api`, `network_refinement.cache_path` (or `--network-refinement-cache-path`) enables shared edge caching across runs/instances.
- `network_plot=dash` launches an interactive Cytoscape-style server; static exports (`plotly`, `cytoscape`) remain the reproducible offline default.
- In module mode, omitted `network_plot` defaults to `plotly`; `none` disables visualization artifacts.
- Dash mode can optionally include a second dataset tab for refinement PPI topology when network refinement is enabled.
- Custom discovery SQLite storage follows `actionConfig.enricher.methyl_enricher_home` (default `/work/cache/methyl_enricher`), with canonical paths:
  - `<methyl_enricher_home>/network_discovery/custom_network.sqlite`
  - `<methyl_enricher_home>/network_discovery/exports/local_edges.csv`
- SQLite schema reference is maintained in `methyl_enricher/sql/network_discovery_schema.sql`.
- Library resolution is deterministic: explicit `libraries` > `library_preset` > built-in defaults.
