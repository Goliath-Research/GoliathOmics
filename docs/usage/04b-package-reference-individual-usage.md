# Package Reference (Individual Usage)

## Purpose

Summarize each active package as a standalone tool, then map it to staged `methyl-validation` orchestration.

For the full SamplePrep story (Picard metrics, Parabricks JSON, guardrails, remediation, extraction QC), see [Chapter 3 — Sample Prep and Quality Control](03-sample-prep-and-qc.md).

## Package quick map

| Package | CLI | Primary role | Typical standalone output |
|---|---|---|---|
| `methylalignmentqc` | `methyl-qc` | Parse alignment/dup metrics into normalized JSON | `alignment_qc/<sample>.json` |
| `methylcentroid` | `methyl-centroid` | Build cohort centroids from methylation files | centroid artifacts per group |
| `methyldetector` | `methyl-detector` | Discover/classify DMPs | `dmps-*-discovery.csv`, `result*.json` |
| `methylmapper` | `methyl-mapper` | Map DMP loci to genes/features | `all-gene_name-combined.csv` |
| `methylenricher` | `methyl-enricher` | Enrichment/module interpretation | `enrichment_merged.csv`, `modules_ranked.csv` |
| `methylclassifier` | `methyl-classifier` | Train production classifier (ECDF path) | `classifiers/*.pkl` |
| `methylpredictor` | `methyl-predictor` | Predict and report metrics/inference | `predictors/*/validation_metrics.json` or blind reports |
| `methylvalidation` | `methyl-validation` | Orchestrate MC/stability/freeze/model workflows | `monte_carlo_runs/*` |
| `methyldiseaseprogression` | `methyl-disease-progression` | Cross-stage synthesis from mapper/enricher outputs | `progression/*.csv`, `summary.json` |

## Standalone examples

### Alignment QC

```bash
source .venv/bin/activate
methyl-qc --metrics-root /data/metrics --output-dir /work/alignment_qc
```

### Enrichment from mapper output

```bash
source .venv/bin/activate
methyl-enricher --input /work/project/mapper/pca_pca1/all-gene_name-combined.csv --modules --outdir /work/project/enricher/pca_pca1
```

### Blind prediction with frozen model

```bash
source .venv/bin/activate
methyl-predictor --project /work/project/monte_carlo_runs/production/project.json
```

## Orchestrated equivalents in `methyl-validation`

- Stability stage runs centroid+detector per iteration.
- Freeze stage runs centroid->detector->mapper->enricher (and optional progression).
- Model stage runs:
  - `ecdf`: classifier->predictor
  - `tabular_sklearn` / `generative_hybrid`: in-process backend train/predict
- Post-model validation runs frozen-model descriptive holdouts.

## Integration diagram

```mermaid
flowchart LR
  prep["Sample prep FASTQ to HDF5"]
  qc["Alignment + extraction QC"]
  stability["MC stability centroid detector"]
  freeze["Freeze fixed panel mapper enricher"]
  model["Model train + predictor"]
  validation["Post-model validation"]
  blind["Blind prediction"]

  prep --> qc --> stability --> freeze --> model --> validation --> blind
```

*Package integration*



## Do not do this

- Do not assume standalone package defaults match project-orchestrated overrides.
- Do not skip `project.json` artifact handoff checks when chaining packages manually.

## See also

- `README.md`
- `packages/*/docs/USAGE.md`
