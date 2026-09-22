# MethylEnricher {#sec-methylenricher}
## Role

`methylenricher` is a downstream interpretation layer. It takes mapped genes or weighted gene lists and organizes them into pathway-level and module-level summaries. The package mixes three kinds of methodology:

- external enrichment analysis through Enrichr [chen2013],
- graph-based pathway grouping,
- optional PPI topology refinement,
- optional interactive Cytoscape-style exploration (Dash),
- heuristic module ranking and disease-prior labeling.

Because of that mixture, this package should be described as a discovery and prioritization layer, not as a single statistical model.

## Over-Representation Analysis

The enrichment step is delegated to Enrichr through `gseapy.enrichr`. In practice this means the repository submits a gene list to an external enrichment engine and then reads back adjusted p-values, odds ratios, and gene memberships for the selected libraries.

The statistical test itself therefore lives outside the repository. The in-repo code is responsible for:

- choosing the input genes,
- selecting the enrichment libraries,
- filtering the returned tables, and
- carrying forward the adjusted p-values into later scoring steps.

Publication text should cite Enrichr [chen2013] and record the exact library set used in the analysis. In MethylEnricher this can be done either by logging an explicit `libraries` list or by logging a named `library_preset` (for example `cancer-extended` for cancer-focused studies).

### Library sets used by MethylEnricher

The package now supports both a standard default set and named cancer presets.

**Standard default (`DEFAULT_LIBRARIES`)**

- `KEGG_2021_Human`
- `Reactome_2022`
- `GO_Biological_Process_2023`
- `GO_Molecular_Function_2023`
- `GO_Cellular_Component_2023`
- `MSigDB_Hallmark_2020`
- `WikiPathway_2023_Human`

**`cancer-core` preset**

Standard default plus:

- `ChEA_2022`
- `ENCODE_and_ChEA_Consensus_TFs_from_ChIP-X`
- `TRRUST_Transcription_Factors_2019`
- `DisGeNET`
- `Jensen_DISEASES`
- `GWAS_Catalog_2019`

**`cancer-extended` preset**

`cancer-core` plus:

- `DSigDB`
- `DGIdb_Drug_Targets_2024`
- `LINCS_L1000_Chem_Pert_up`
- `LINCS_L1000_Chem_Pert_down`
- `miRTarBase_2017`

When both `libraries` and `library_preset` are provided, explicit `libraries` take precedence.

## Pathway Graph Construction

The package builds a pathway graph in which nodes are pathways and edges represent gene-set overlap. In the simplest case the edge weight is based on Jaccard similarity:

<div id="eq-jaccard" markdown="1">

$$
J(A,B) = \frac{|A \cap B|}{|A \cup B|},
$$

</div>

or a closely related overlap score. Community detection is then performed with the Louvain method [blondel2008], with connected-components fallbacks if the required package is unavailable.

This is a standard graph heuristic for organizing pathways, but the resulting modules depend materially on the chosen threshold, graph construction rule, and Louvain resolution behavior.

When broad library presets are used (for example `cancer-extended`), module construction can include tens of thousands of enrichment terms. The implementation now supports pre-clustering term reduction so modules can be built from a focused subset:

- q-value gate (`module_cluster_max_q`): keep only terms with adjusted p-value below a threshold,
- per-library cap (`module_cluster_top_terms_per_library`): keep top N terms per library before graph construction.

These controls change the module graph itself, not only the display layer, so they should be reported in methods sections.

## Why This Layer Matters For DMP Validation

`methylmapper` and `methylenricher` are not upstream feature selectors, but they are the main biological validation layer for the selected DMP panel. In practice, if the detector-selected loci are biologically coherent, downstream enrichment should recover:

- lineage-consistent terms (for prostate cancer: prostate epithelial identity programs),
- stage-consistent pathway transitions (for example increasing mitochondrial/OXPHOS emphasis in later disease),
- reproducible themes across mapping, enrichment, and module summaries.

This is not formal proof of causal correctness, but it is a strong orthogonal consistency check on DMP/gene selection quality.

## Module Enrichment Score

`compute_module_enrichment_score()` converts a module into a scalar ranking score by combining:

- mean $-\log_{10}(q)$ across pathways,
- number of pathways,
- number of genes,
- mean gene weight.

In normalized form the score is

<div id="eq-module-score" markdown="1">

$$
\operatorname{score}_{\text{enrich}}
=
0.4\,\tilde m_{-\log_{10} q}
+ 0.2\,\tilde n_{\text{pathways}}
+ 0.2\,\tilde n_{\text{genes}}
+ 0.2\,\tilde m_{\text{gene-weight}},
$$

</div>

where each tilded quantity is clipped to $[0,1]$ after dividing by a hand-chosen scale such as $10$, $50$, or $200$.

This is explicitly heuristic. It is a ranking function, not an enrichment test.

## Disease Relevance Prior

The package also computes a disease-relevance score by overlap with a curated gene set:

<div id="eq-disease-prior" markdown="1">

$$
\operatorname{score}_{\text{disease}}
=
\frac{|G_{\text{module}} \cap G_{\text{disease}}|}
{|G_{\text{module}}|}.
$$

</div>

When no disease set is available the package returns a neutral value of $0.5$. By default the code ships with a prostate-cancer-oriented prior gene set, so that default must not be described as disease-agnostic.

The final module score is then

<div id="eq-final-module-score" markdown="1">

$$
\operatorname{score}_{\text{final}}
=
0.7\,\operatorname{score}_{\text{enrich}}
+ 0.3\,\operatorname{score}_{\text{disease}}.
$$

</div>

This is again a heuristic prioritization rule.

## Optional PPI Network Refinement

When enabled (`network_refinement.enabled=true` or matching CLI flags), the package adds a second graph layer from gene-level protein-protein interactions (STRING API or a local edge list). For each module it computes:

- induced-subgraph density,
- mean of a **per-gene node metric** on the subgraph (by default the signal-weighted combined hub score, not only raw degree centrality),
- largest connected-component ratio.

Raw PPI hubs are often dominated by high-database-degree “infrastructure” genes. By default, node metrics are combined with **input methylation weights** (e.g.\ `mean_effect_size` / `gene_importance` from the mapper CSV): normalized centrality terms are blended into `topology_score`, then multiplied by min--max normalized weights and an optional disease-prior boost to form `combined_hub_score`. Hub lists and module coherence use this score unless `hub_ranking_mode=topology`.

These features are combined into a heuristic PPI coherence score:

<div id="eq-ppi-coherence" markdown="1">

$$
\operatorname{score}_{\text{ppi}}
=
0.4\,\operatorname{density}
+ 0.3\,\operatorname{mean\_node\_metric}
+ 0.3\,\operatorname{largest\_component\_ratio}.
$$

</div>

The final ranking can then be blended with weight $w \in [0,1]$:

<div id="eq-blended-module-score" markdown="1">

$$
\operatorname{score}_{\text{blended}}
=
(1-w)\,\operatorname{score}_{\text{final}}
+ w\,\operatorname{score}_{\text{ppi}}.
$$

</div>

This extension is still heuristic. It should be reported as structural corroboration of module coherence, not as formal inference or disease-association evidence.

## Visualization Modes

The package supports both reproducible offline artifacts and interactive inspection:

- offline: Plotly HTML and Cytoscape.js HTML+JSON exports,
- interactive: optional `dash_cytoscape` viewer (`network_plot=dash`) with module and edge-threshold filtering.

For publication and auditability, offline artifacts should remain the canonical archived output; Dash should be treated as an exploratory interface.

## Project-Orchestrated Execution

In pipeline runs, MethylEnricher is commonly launched with `--project` and reads merged `actionConfig.enricher`.
When the project contains multiple comparison groups, the runtime can execute one enrichment/module pass per group using per-comparison mapper inputs and enricher outputs. If explicit input/output arguments are passed, execution falls back to a single run.

Configuration precedence for libraries is:

1. explicit `libraries`,
2. `library_preset`,
3. built-in defaults.

This precedence should be reported in methods text to ensure reproducibility of enrichment calls.

## Theme Normalization And Labels

The package includes theme normalization based on substring matching against a JSON mapping file, plus discrete labels such as `High`, `Medium`, and `Low` relevance. Those labels are workflow conveniences. They should not be presented as probabilistic classifications.

## Publication Guidance

The most accurate description is:

- enrichment significance comes from an external ORA engine,
- pathway modules come from graph clustering over pathway overlap,
- optional PPI refinement comes from STRING or local edge lists with explicit score thresholds and community settings,
- module ranking is heuristic,
- and disease relevance is a curated prior overlap rather than a learned posterior.

That combination can be highly useful for interpretation, but it should be framed as an annotation and prioritization pipeline rather than as formal inference.
