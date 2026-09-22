# MethylDiseaseProgression

Aggregate per-comparison MethylMapper/MethylEnricher outputs into a single
disease-stage progression report.

## Documentation

- [`docs/USAGE.md`](docs/USAGE.md) - CLI usage, inputs/outputs, and workflow integration.
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) - internal data flow and file contracts.
- [`docs/THEORY.md`](docs/THEORY.md) - progression scoring/labeling conventions and caveats.

## CLI

```bash
methyl-disease-progression --project /path/to/project.json
```

Optional flags:

- `--output-dir`: override default progression output directory.
- `--ordered-comparison-labels`: explicit disease-group order (comma-separated).
- `--strict-missing`: fail if any stage output is missing.
- `--report-md`: also write a human-readable markdown report.

## Project settings (profile `actionConfig.progression`)

The CLI reads progression settings from the merged profile `actionConfig.progression`:

```json
"actionConfig": {
  "progression": {
    "enabled": true,
    "ordered_comparison_labels": ["pca_pca1", "pca_pca2", "pca_pca3", "pca_pca4"],
    "strict_missing": false,
    "report_md": true
  }
}
```

## Inputs

For each project comparison (`control_group`, `disease_group`), the tool reads:

- Mapper combined genes: `mapper/<control>/<disease>/all-gene_name-combined.csv`
  (or profile `actionConfig.enricher.combined_csv_name`).
- Enrichment table: `enricher/<control>/<disease>/enrichment_merged.csv`.
- Optional modules table: `enricher/<control>/<disease>/modules_ranked.csv`.

## Outputs

Default output directory: `<project_root>/progression`.

- `genes_long.csv` — long table with columns: `stage_index`, `comparison` (canonical stage token from the project’s ordered comparisons), `rank`, `score`, `gene`.
- `pathways_long.csv` — same layout with `pathway` instead of `gene`. Pathway `score` is derived from the enrichment table (e.g. `-log10(adjusted p)` when q-values are available); raw q-values are not exported in this file.
- `modules_long.csv` — same layout with `module` (empty file / no rows if no `modules_ranked.csv` inputs).
- `entities_progression_labels.csv` — aggregated progression labels per entity (`entity_type`, `entity_id`, `entity_label`, …) built from the long tables above.
- `summary.json`
- optional `report.md`

Legacy long-table columns (`entity_type`, `entity_id`, `entity_label`, `comparison_label`, `disease_group`, `control_group`, `q_value`, `source_file`) are no longer written to the `*_long.csv` files.
