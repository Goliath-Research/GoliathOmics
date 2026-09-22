# methyl_deconv usage

Leukocyte cell-type deconvolution for buffy-coat WGBS via Houseman constrained projection onto the published **FlowSorted.Blood.EPIC** IDOL marker library.

## CLI

```bash
source .venv/bin/activate
methyl-cell-deconv --project /work/projects/<study>/configs/project_Buffy_*.json \
  --resolved-config /path/to/resolved.json
```

Writes `{output_base}/cell_fractions/cell_fractions.csv` with columns:

`sample_id`, `group`, `CD8T`, `CD4T`, `NK`, `Bcell`, `Mono`, `Neu`, plus marker diagnostics.

`group` is the resolved project subgroup label returned by `get_resolved_groups()`
(for example, `all` or `PCa`). Existing output files are not modified in place;
rerun `methyl-cell-deconv` to produce the column.

**Covariate use:** when pointing `covariates_path` at this CSV, do **not** feed `group`
(or `qp_status` / marker diagnostics) into models — that leaks the cohort label.
Pin `covariate_numeric_columns` to the six Ω columns (as in `cell_deconv.profile.json`),
or rely on methylvalidation auto-infer which excludes those metadata columns.

## Config (`actionConfig.cell_deconvolution`)

| Field | Role |
|-------|------|
| `seed_basis_path` | Override packaged IDOL JSON (site/profile) |
| `contexts` | Required (e.g. `["CG"]`) — set in site/profile |
| `marker_min_coverage` | Required min coverage at marker CpGs |
| `min_marker_fraction` | Required min fraction of markers observed |
| `use_gpu` | MethylUtils CuPy path when available |

Missing `contexts` / `marker_min_coverage` / `min_marker_fraction` raises when building
`CellDeconvRuntimeParams` from the step config (no code fallbacks). Core APIs take that
typed Pydantic model — not loose kwargs.

## DomainProgram / profile

- Program: `workflow_engine/domain/fixtures/cell_deconv_tabular.program.json`
- Profile: `workflow_engine/domain/profiles/cell_deconv.profile.json` (tabular + Ω/clinical covariates_path)

Rebuild seed basis (dev): `python -m methyl_deconv.scripts.build_flowsorted_idol_basis`
