# MethylEnricher Usage

## CLI Entry Points

The package exposes:

- `methyl-enricher`
- `methyl_enricher`
- `methyl-enricher-network-discovery`
- `methyl_enricher_network_discovery`

- `methyl-enricher` / `methyl_enricher` resolve to `methyl_enricher.cli:main`.
- `methyl-enricher-network-discovery` / `methyl_enricher_network_discovery` resolve to `methyl_enricher.network_discovery_cli:main`.

## Main run modes

`methyl-enricher` supports three practical modes:

- single-input enrichment (`--input`, optional filters and library controls),
- module pipeline mode (`--modules`) with pathway clustering and ranking outputs,
- project-driven mode (`--project`) that resolves input/output paths from pipeline config.

When `--project` points to a project with multiple cancer-group comparisons and no explicit `--input`/`--outdir` overrides, the CLI runs once per comparison using:

- mapper input from `mapper/<control>/<disease>/...combined.csv`,
- output under `enricher/<control>/<disease>/`.

Passing explicit `--input` or non-default `--outdir` with `--project` forces a single run.

## Library selection precedence

Library resolution is explicit and stable:

- `--libraries` (or `actionConfig.enricher.libraries`) wins,
- else `--library-preset`,
- else package defaults.

### Library presets (registry-backed)

Presets are no longer hardcoded in Python. They are authored in the committed registry
`methyl_enricher/data/library_presets.json` (validated by
`methyl_enricher.preset_registry.LibraryPresetCatalog`, JSON Schema
`schemas/config/library_presets.schema.json`) and can be synced into the `cfg` registry
like the action catalog:

```bash
methyl-cfg sync-library-presets   # registry -> cfg kind enrichment_library_preset
methyl-cfg materialize            # -> /work/site/enrichment/
```

Shipped presets:

- `cancer-core` / `cancer-extended` — oncology (pathways + TF regulators + disease priors, plus drug/perturbation/miRNA sets in extended).
- `neuro-core` — neurodegeneration / CNS (adds brain/CNS tissue and cell-type gene sets and aging perturbations; drops oncology-only drug libraries). Used by the Alzheimer cfDNA pack.

Add a preset by editing `library_presets.json` and re-running the schema export
(`scripts/export_config_schemas.sh`) — no code change. Unknown preset names still raise
a clear `ValueError` at resolve time, and `methyl-enricher --library-preset` choices are
derived from the registry.

## CIS-BP transcription-factor motifs (optional)

In addition to the Enrichr libraries, MethylEnricher can incorporate
[CIS-BP](https://cisbp.ccbr.utoronto.ca) transcription-factor motifs. CIS-BP is a
catalog of TFs and their DNA-binding motifs (PWMs) — it is **not** an Enrichr-style
gene-set service, so the integration is pluggable via a `mode`:

- `gene_sets` (default, **implemented**): derive TF → target-gene sets and run
  over-representation analysis (ORA) on your gene list, emitting an
  `enrich_CIS-BP.csv` that merges alongside the Enrichr libraries (categorized as a
  `tf` library in the module pipeline).
- `annotate`: annotate TFs already surfaced by ChEA/ENCODE/TRRUST (or other TF
  libraries present in the output directory) with CIS-BP motif IDs, evidence, and
  family metadata. Requires those Enrichr libraries to run **before** CIS-BP in the
  same output folder.
- `motif_scan`: scan DMP/DMR region sequences with CIS-BP PWMs for direct motif
  enrichment at differentially methylated loci.

CIS-BP has no query API, so the per-species archive is **auto-downloaded** from the
bulk-download endpoint on first use and cached (default cache:
`actionConfig.enricher.methyl_enricher_home` or `~/.methyl_enricher`).

For `mode=gene_sets` with the default `gene_set_source=promoter_scan`, TF → target
sets are built by scanning gene **promoter** sequences with the CIS-BP PWMs, which
needs a genome FASTA and a GTF. These default to the project's existing shared
properties, so you do not repeat them in the CIS-BP block:

- `genome_fasta` ← `actionConfig.alignment_qc.genome_fasta` (the project's reference genome),
- `gtf` ← `actionConfig.mapper.gtf` (falling back to the `GENE_GTF` env var).

Set `cisbp.genome_fasta` / `cisbp.gtf` only to override those project defaults. The
built GMT is cached so subsequent runs are fast. You can also supply a prebuilt GMT
directly via `gene_set_source=prebuilt_gmt` + `gmt_path`.

For **cfDNA** projects, set `validation.regulatory.primary_analyte` to `cfdna` and the
[pipeline analyte profile](../../docs/ANALYTE_PROFILES.md) enables CIS-BP with
`cisbp_modes: [gene_sets, motif_scan, annotate]` (three merge labels). Override in
`actionConfig.enricher.cisbp` as needed.

Enable via CLI (`--cisbp`, optional `--cisbp-mode`) or in the pipeline profile under
`actionConfig.enricher.cisbp`:

```json
{
  "actionConfig": {
    "alignment_qc": {
      "genome_fasta": "/path/to/hg38.fa"
    },
    "mapper": {
      "gtf": "/path/to/annotation.gtf"
    },
    "enricher": {
      "library_preset": "cancer-extended",
      "cisbp": {
        "enabled": true,
        "mode": "gene_sets",
        "species": "Homo_sapiens",
        "motif_evidence": ["Direct", "Inferred"],
        "gene_set_source": "promoter_scan",
        "promoter_upstream": 5000,
        "promoter_downstream": 200,
        "motif_score_threshold": 0.85,
        "min_targets_per_tf": 5
      }
    }
  }
}
```

Quick toggles `cisbp_enabled` / `cisbp_mode` are also accepted as flat keys. A CIS-BP
failure (network, missing FASTA, a planned mode) is treated as a soft warning and
never breaks the core Enrichr enrichment.

For statistically cleaner ORA, point `gene_universe_file` at the full set of mapped
genes (the promoter scan and ORA background are then defined over that universe);
otherwise the foreground gene list is used with a default background size.

## Module + network outputs

With `--modules`, MethylEnricher runs enrichment + pathway graph clustering and writes `modules_ranked.csv`.

Module naming is disease-agnostic by default:

- Primary labels are derived from canonical library evidence (KEGG/Reactome/GO/Hallmark/WikiPathways) when present.
- Perturbation/drug libraries (for example LINCS/DSigDB) are retained as supporting evidence, not primary names.
- `modules_ranked.csv` now includes:
  - `Module_primary` (canonical label),
  - `Module_supporting_perturbation` (top perturbation signatures),
  - `Module_display` (presentation label).
- Use `--module-label-mode canonical_only|dual_label` (or `actionConfig.enricher.module_label_mode`) to control display behavior. Default: `dual_label`.

Disease columns in module outputs are conditional:

- When a disease prior can be inferred from mapper-style disease columns in the input CSV (or explicitly provided by callers), `modules_ranked.csv` includes `Disease_relevance_score` and `Disease_relevance_tier`.
- When no valid disease prior exists (for example healthy-vs-healthy runs, or disease filters that leave zero prior genes), disease columns are omitted to avoid misleading interpretation.
- Disease-related CLI filters (`--disease-only`, `--min-disease-score`, `--min-disease-evidence-level`, etc.) can narrow both input genes and inferred module disease prior when disease metadata exists.
- Use `--cluster-seed` to make Louvain module assignments reproducible across runs with identical inputs/parameters.
- When mapper exports `hits_*` columns, enricher computes a default feature-weight score from SP weights (`promoter=2.0`, `exon=1.5`, `gene_body=1.0`, `intron=0.7`, `terminator=0.5`) and uses it as the default gene weight.

`--network-plot` behavior:

- default when omitted in module mode: `plotly`,
- `none`: disable exports,
- `plotly`, `pyvis`, `cytoscape`, `all`: offline artifacts,
- `dash`: interactive server (`--dash-host`, `--dash-port`, optional `--dash-open-browser`).

Optional PPI refinement can be enabled via CLI flags or `actionConfig.enricher.network_refinement`:

- `source=string_api` (default) or `source=local_edges`,
- writes `ppi_network_edges.csv`, `ppi_node_metrics.csv`, `ppi_hubs.csv`, `ppi_module_coherence.csv`,
- adds blended scoring columns in `modules_ranked.csv` (`Base_score`, `PPI_coherence_score`, `Blended_score`).

Hub ranking and module-level PPI coherence use **methylation signal** by default (`hub_ranking_mode=signal_weighted`, CLI `--network-refinement-hub-ranking-mode`):

- `ppi_node_metrics.csv` includes `methylation_weight`, `topology_score`, and `combined_hub_score` (topology × normalized input weight × optional disease boost).
- `ppi_hubs.csv` is sorted by `combined_hub_score` unless you set `hub_ranking_mode=topology` (pure graph centrality).
- `ppi_module_coherence.csv` includes `ppi_mean_combined_hub_score` when signal-weighted mode is active, and the coherence blend uses that metric instead of raw degree centrality alone.
- Optional: `hub_disease_boost` scales genes in the disease prior; `hub_w_degree` / `hub_w_betweenness` / `hub_w_closeness` weight the three normalized centrality terms inside `topology_score` (defaults: equal thirds).

## Custom network discovery workflow

The network discovery CLI scans completed enricher outputs (for example directories containing
`ppi_network_edges.csv` and `modules_ranked.csv`), computes candidate edges and novelty against STRING,
writes a versioned SQLite snapshot, and exports curated edges in `source,target,score` format for
`network_refinement.source=local_edges`.

By convention, persistent artifacts resolve under:

- `actionConfig.enricher.methyl_enricher_home`
- default fallback: `/work/cache/methyl_enricher`

Paths created by default:

- `<methyl_enricher_home>/network_discovery/custom_network.sqlite`
- `<methyl_enricher_home>/network_discovery/exports/local_edges.csv`

Example:

```bash
methyl-enricher-network-discovery \
  --project /path/to/project.json \
  --scan-root /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/enricher
```

## Profile actionConfig mapping

Profile `actionConfig.enricher` maps onto CLI arguments through `EnricherStepConfig`.

- Both nested `network_refinement.{...}` and flat `network_refinement_*` keys are supported.
- For `network_plot`, explicit CLI value wins over config.
- In project mode, `--step-override` can inject temporary step-level overrides without editing the project JSON.

## Typical Inputs

Typical runs start from:

- mapped genes or weighted gene lists from `methylmapper`,
- optional disease-specific prior genes,
- a selected set of Enrichr libraries and pathway-theme mappings.

## Typical Outputs

The package can emit:

- filtered enrichment result tables,
- pathway-overlap graphs,
- module rankings,
- disease-relevance summaries and pathway-theme labels.

## Ensure-complete mode

Use **`--ensure-complete`** on production projects so every Enrichr library is present before progression or modeling:

```bash
methyl-enricher --project /path/to/monte_carlo_runs/production/project.json --ensure-complete
```

- Retries transient Enrichr failures (429, parse errors) with exponential backoff.
- Skips libraries whose `enrich_<library>.csv` already exists (use `--force` to re-query).
- Writes `enricher_task_status.json` per comparison and `enricher/enricher_completeness.json` at the enricher root.
- Exit **0** only when all comparisons are complete; otherwise **1**.

Config (`actionConfig.enricher`):

| Key | Default | Purpose |
|-----|---------|---------|
| `ensure_complete` | false | When true with `--project`, same as CLI flag |
| `enricher_max_retries` | 5 | Per-library attempts |
| `enricher_retry_base_seconds` | 30 | Initial backoff |
| `enricher_retry_max_seconds` | 600 | Backoff cap |
| `enricher_inter_library_delay_seconds` | 2 | Pause between libraries |
| `distributed` | false | Freeze plans tasks only (see queue below) |

Other flags: `--verify-only`, `--comparison LABEL`, `--no-retry`.

## Distributed enricher queue (per comparison)

After freeze mapper outputs exist:

```bash
methyl-enricher plan-tasks --project .../production/project.json
methyl-enricher export-queue --project .../production/project.json
# workers (one comparison each):
methyl-enricher run-task --task .../enricher/queue/tasks/enricher_PCa_PCa1.json
methyl-enricher verify-complete --project .../production/project.json
```

Set `actionConfig.enricher.distributed: true` in freeze config to run **plan-tasks** during `--freeze` instead of blocking on Enrichr (workers must finish before progression).

## Related Documentation

- Theory: [`THEORY.md`](THEORY.md)
- Implementation: [`IMPLEMENTATION.md`](IMPLEMENTATION.md)
