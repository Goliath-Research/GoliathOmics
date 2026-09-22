# Plasma / cfDNA retrain path

Use this workflow when the cohort is **plasma or cell-free DNA**, not buffy-coat WBC DNA. The default MethylPipeline examples (`Healthy_vs_PCa*`) train on buffy coat; applying those models to plasma without retraining causes domain shift.

## 1. Declare analyte in project JSON

```json
"actionConfig": {
  "validation": {
    "regulatory": {
      "primary_analyte": "cfdna",
      "model_training_analyte": "cfdna",
      "sample_type": "Plasma cfDNA WGBS",
      "stage": "feasibility"
    },
    "enforce_training_analyte_match": true
  },
  "alignment_qc": {
    "fragmentomics": { "enabled": true, "profile": "cfdna" },
    "auto_profile_from_analyte": true,
    "bisulfite_conversion": { "enabled": true, "source": "auto" }
  },
  "fragmentomics": { "enabled": true, "modes": ["wps", "end_motifs"] },
  "enricher": {
    "cisbp": { "enabled": true, "mode": "gene_sets" }
  }
}
```

- `model_training_analyte`: matrix used for stability, freeze, and `--model` (defaults to `primary_analyte`).
- `enforce_training_analyte_match`: when true, `--model` **fails** if `locked_model_spec.json` was built for a different analyte (e.g. buffy_coat).

## 2. Upstream QC and extraction (before HDF5)

Recommended order (SamplePrepPipeline) — see [`workflow_engine/sql_mssql/SamplePrepFlow.md`](../../../workflow_engine/sql_mssql/SamplePrepFlow.md):

1. Download FASTQs → sample directory under `/work/samples/{id}/` (any structured laboratory source)
2. Parabricks (GPU) → BAM + `*.qc-metrics.tar` + `{sample}.json`
3. `methyl-qc` — alignment guardrails + cycle screening; **`qcPass`** gates extract; optional fastp remediation
4. Delete FASTQs (after **final** alignment QC — retained through trim/realign if remediation runs)
5. **If QC pass and `primary_analyte` is `cfdna`:** `methyl-fragmentomics` (BAM WPS + end motifs)
6. MethylExtractor (GPU) → `{chrom}-{ctx}.h5` + `{sampleId}.extraction_manifest.json`
7. `methyl-extraction-qc` — post-extract guardrails; **`extractionQcPass`** gates archive/cleanup
8. Optional `sample.upload-h5` → durable archive
9. Delete BAM (after extraction QC pass)

**Why QC before fragmentomics:** failed samples should not spend hours scanning BAMs. **buffy_coat** projects skip fragmentomics.

**Deploy workflow:** `bash scripts/deploy_workflow_definitions.sh` (DomainProgram [`sample_prep.program.json`](../../../workflow_engine/domain/fixtures/sample_prep.program.json)).

### Bisulfite conversion sidecar

Per sample directory, add `bisulfite_conversion.json`:

```json
{
  "conversion_rate_pct": 99.2,
  "non_cpg_methylation_pct": 0.9,
  "source": "lambda_spikein",
  "notes": "Picard/spike-in report 2026-03-01"
}
```

Without a sidecar, alignment QC uses the **deamination qscore proxy** only (qualitative).

## 3. Standard Workflow 1 on plasma cohort

Same commands as buffy coat, but centroids and panels are built **only from plasma sample paths** in `project.json`:

```bash
source .venv/bin/activate
methyl-validation --project configs/project_Plasma_cfDNA_CG.example.json --stability
methyl-validation --project configs/project_Plasma_cfDNA_CG.example.json --freeze
methyl-validation biological-readiness <project_root>
methyl-validation --project configs/project_Plasma_cfDNA_CG.example.json --model
```

`locked_model_spec.json` records `model_training_analyte: cfdna` for traceability.

## 4. Legacy plasma centroid configs

[`packages/methylcentroid/configs/plasma_*`](../../methylcentroid/configs/) and `data/plasma_*.csv` are **experimental** one-off centroid builds. Prefer a full `project.json` plasma cohort and the workflow above for production traceability.

## 5. Do not mix analytes

| Action | Buffy project | Plasma/cfDNA project |
|--------|---------------|----------------------|
| Use example `Healthy_vs_PCa*.json` | Yes | No |
| `--model` after buffy freeze | Yes | No (enable `enforce_training_analyte_match`) |
| Classifier interpretation | Host-response framing | Tumor-derived / cfDNA framing |

CIS-BP TF enrichment (`enricher.cisbp`) is analyte-agnostic at the gene level; fragmentomics and regulatory metadata are not.
