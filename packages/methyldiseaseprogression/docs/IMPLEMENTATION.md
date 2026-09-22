# MethylDiseaseProgression Implementation Notes

## Core Flow

Entry point:

- `methyl_disease_progression/cli.py` -> `run_progression_report(...)`

Main implementation:

- `methyl_disease_progression/progression.py`

High-level sequence:

1. Resolve ordered stage specs from project comparisons (`resolve_stage_specs`).
2. Build long-form tables per stage:
   - genes (`_build_gene_rows`)
   - pathways (`_build_pathway_rows`)
   - modules (`_build_module_rows`)
3. Concatenate and write long tables.
4. Compute label summaries (`label_progression_entities` path in module).
5. Optionally compute gene-set metrics (`gene_set_coverage.py`, `gene_set_metrics.py`).
6. Write `summary.json` and optional markdown report.

## Stage Resolution Contract

`resolve_stage_specs(...)` builds per-stage file paths using `ProjectConfig` accessors:

- mapper output dir: `project.get_mapper_output_dir(control_group, disease_group)`
- enricher output dir: `project.get_enricher_output_dir(control_group, disease_group)`

This keeps progression aligned with production folder conventions and comparison order semantics.

## Missing-Input Handling

- Default mode records missing inputs in summary and continues.
- `strict_missing=True` raises a failure when required stage inputs are absent.
- `_enricher_completeness_missing(...)` can surface missing-library context when an enricher completeness manifest exists.

## Output Schema Intent

Long tables are intentionally simple and analysis-friendly:

- stage index
- comparison token
- rank
- score
- entity column (`gene` / `pathway` / `module`)

Entity-level labels are emitted separately in `entities_progression_labels.csv` to avoid overloading the long-table contract.

## Dependencies

- `pandas` for table loading/aggregation/writing.
- `methylutils` for canonical project parsing/comparison resolution.

The package stays file-centric and does not call external LLM services.
