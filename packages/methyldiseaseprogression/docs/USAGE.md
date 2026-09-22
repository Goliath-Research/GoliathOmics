# MethylDiseaseProgression Usage

## Overview

`methyl-disease-progression` synthesizes ordered cross-stage summaries from production
`methyl-mapper` and `methyl-enricher` outputs. It writes long-form CSVs, progression labels,
and a summary JSON under a progression output directory.

## CLI

```bash
methyl-disease-progression --project /path/to/project.json
```

Common options:

- `--output-dir`: override default `<project_root>/progression`
- `--ordered-comparison-labels`: comma-separated explicit order
- `--strict-missing`: fail when expected stage inputs are missing
- `--report-md`: write `report.md`
- `--gene-set-metrics` / `--no-gene-set-metrics`: override progression gene-set metrics behavior
- `--gene-sets-path` / `--gene-set-profile`: provide external profile JSON
- `--disease-profile`: use bundled disease profile key

## Configuration (`actionConfig.progression`)

Progression settings resolve from **profile/site `actionConfig.progression`** (merged into instance `context_json` / production `project.json` snapshots). The study manifest does not carry `step_config`.

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

Order resolution precedence:

1. CLI `--ordered-comparison-labels`
2. `actionConfig.progression.ordered_comparison_labels` / `ordered_disease_groups`
3. Project comparison order from `methylutils` `ProjectConfig`

## Inputs Per Comparison

For each resolved comparison `(control_group, disease_group)`:

- Mapper combined CSV:
  - `mapper/<control>/<disease>/all-gene_name-combined.csv`
  - or `actionConfig.enricher.combined_csv_name`
- Enricher merged pathway CSV:
  - `enricher/<control>/<disease>/enrichment_merged.csv` (fallback to `enrichment_top_q0.05.csv`)
- Optional modules CSV:
  - `enricher/<control>/<disease>/modules_ranked.csv`

If `--strict-missing` is enabled, missing inputs fail the run.

## Outputs

Default output directory: `<project_root>/progression`

- `genes_long.csv`
- `pathways_long.csv`
- `modules_long.csv` (may be empty if module inputs are absent)
- `entities_progression_labels.csv`
- `summary.json`
- optional `report.md`

`summary.json` includes row counts, ordered comparison labels, missing-input diagnostics,
and optional gene-set metrics metadata when enabled.

## Typical Integration

In the standard `methyl-validation --freeze` stage, progression can run automatically when
`actionConfig.progression.enabled=true`. You can also run it manually:

```bash
methyl-disease-progression \
  --project /work/projects/prostate-cancer/Healthy_vs_PCa1-5-CG/monte_carlo_runs/production/project.json \
  --strict-missing \
  --report-md
```
