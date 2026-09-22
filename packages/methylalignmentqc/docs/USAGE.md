# MethylAlignmentQC Usage

## CLI Entry Points

The package exposes:

- `methyl-alignment-qc`
- `methyl-qc`
- `methyl-qc-migrate-guardrails`
- `methyl-qc-export-schema`
- `methyl-qc-convert-v1-to-v2`

## Main execution modes

`methyl-qc` requires exactly one input mode:

- `--project` (optionally `--step-override`) to resolve samples/output from project config,
- `--samples` (repeatable; can be a sample dir, newline list file, or JSON array file),
- `--metrics-root` (auto-discovers `*deduplicate_metrics.txt` and derives sample dirs).

For `--samples` and `--metrics-root`, `--output-dir` is required.

Useful flags:

- `--no-validation`: skip schema validation,
- `--verbose`: print sample counts and output path.

## Mode-aware metrics families

`methyl-qc` detects a **metrics family** (`methyl_alignment_qc/core/metrics_family.py`) from task `alignmentMode` when present, otherwise from artifacts:

| Mode | Family | Required artifacts | Family-specific guardrails |
|------|--------|--------------------|----------------------------|
| `linear` / `pangenome` (Clara) | Parabricks | `{id}.json` or `qc-metrics.tar` with `quality_yield`; dedup metrics | `wgbs_parabricks_qc.py` + cycle screening (+ fragmentomics when enabled) |
| `linear` (MojoFq2bamMeth) | Mojo linear | `{id}.json` with `metrics_source=samtools+placeholders` | `mojo_linear_qc.py` — PF%/Q30/mapped only; placeholders not Clara-equivalent hard fails |
| `pangenome_wgbs` | methylGrapher WGBS | `{id}.alignment_metrics.json` (+ optional Picard when `collectmultiplemetrics`) | `wgbs_pangenome_qc.py` |
| `pangenome_wgbs` | methylGrapher | `{id}.alignment_metrics.json` (`tool=methylGrapher`), GAF, QC BAM, dedup | `wgbs_pangenome_qc.py` (provenance / GAF / BAM / mapped rate); Parabricks core only if provenance `collectmultiplemetrics: true` |

**Shared** across modes: Picard dedup → `summary_stats`; optional duplication/PF; alignment-derived rates; optional `samtools flagstat`; bisulfite sidecar; `overall_pass` = AND of evaluated details.

**Inference hazard:** when `alignmentMode` is omitted and both a Picard tar and methylGrapher provenance exist, inference can prefer Parabricks. Prefer an explicit mode on the worker task input. Operator detail: [Usage ch.03](../../../docs/usage/03-sample-prep-and-qc.md); theory: [ch.09](../../../docs/theory/chapters/09-methylalignmentqc.md).

## Typical Inputs

Typical runs require:

- one or more sample directories or a metrics root,
- Picard-style duplication metrics (all modes),
- **either** Parabricks `{sample_name}.json` / `qc-metrics.tar` **or** methylGrapher `{sample_name}.alignment_metrics.json` + GAF (+ optional Picard enrichment),
- an output directory for normalized JSON summaries.

## Typical Outputs

The package writes one slim **V2.1 guardrail-summary** JSON per sample (`export_kind: guardrail_summary`) and can optionally validate the in-memory V1-shaped assembly (including Picard tables) before converting for export.
For the Parabricks family, when `{sample_name}.json` is present, `methyl-qc` uses it to **compute** metrics and guardrails; histograms stay in that native file (or `{sample_name}.qc-metrics.tar`). The written output in `--output-dir` is always the slim V2.1 export.

For Parabricks-family pre-checks, the standalone `wgbs_parabricks_qc.py` utility can write a `guardrails` block into the input JSON (or into `--output` if specified). This block includes each metric's `value`, `normal_range`, pass/fail state, and a user-facing `message` that explains why the guardrail matters.

## Bisulfite conversion (automated)

Enable in `actionConfig.alignment_qc.bisulfite_conversion` (profile/site). Place `bisulfite_conversion.json` in each sample directory:

```json
{
  "conversion_rate_pct": 99.2,
  "non_cpg_methylation_pct": 0.8,
  "source": "lambda_spikein"
}
```

`methyl-qc` adds `bisulfite_conversion_metrics` and, when the sidecar supplies rates, nested `guardrails.details.bisulfite_conversion` (`conversion_rate_pct` / `non_cpg_methylation_pct` only). Deamination votes only as `guardrails.details.deamination_qscore`. With `source: auto` and a missing sidecar, `measurement_source` / `notes` record that deamination is a qualitative proxy — they do **not** nest a second `deamination_qscore` under the conversion heading.

## Read 2 cycle screening and remediation

When `cycle_screening.enabled` is true (default), each export includes `guardrails.screening` with a disposition:

| Disposition | Meaning |
|-------------|---------|
| `USE_CURRENT_ALIGNMENT` | QC passed |
| `REALIGN_READ2_TRIM` | Fixable Read 2 start dip; see `trim_front2` |
| `INVESTIGATE_MULTI_REGION` | Multiple low-quality regions |
| `INVESTIGATE_GUARDRAIL_ONLY` | Cycles OK; check duplication/depth/insert size |
| `NOT_FIXABLE` | No automated trim path |

Optional config under `actionConfig.alignment_qc`:

```json
{
  "cycle_screening": { "r2_quality_threshold": 30, "max_trim_bases": 8 },
  "optional_guardrails": { "duplication_rate_max": 0.25, "min_pf_reads": 1000000 },
  "alignment_guardrails": {
    "enabled": true,
    "min_mapping_rate": 0.98,
    "max_secondary_supplementary_rate": 0.05,
    "min_gc_coverage_uniformity": 0.5,
    "flagstat_enabled": true,
    "min_properly_paired_rate": 0.90,
    "max_supplementary_rate_flagstat": 0.02
  }
}
```

Alignment guardrails are enabled by default for `cfdna` and `buffy_coat` via the analyte profile. Tune thresholds with `scripts/calibrate_alignment_guardrails.py`. GPU workers need **samtools** on PATH when `flagstat_enabled` is true.

Each QC run appends to `qc_history` in the export JSON. Retry QC (after `sample.trim_fastq` + forced realign) must pass `qcAttempt`, `qcAttemptReason`, and `remediationTrigger` in the worker task input. SamplePrep actions also append to `{sampleDir}/{sampleId}.sample_prep_log.jsonl`.

Cohort screening without re-align:

```bash
python scripts/alignment_qc_cohort_screening.py \
  --qc-dir /work/AlignmentQC \
  --group healthy=/path/healthy.csv --group pca=/path/pca.csv \
  --out /work/AlignmentQC/screening_report
```

Default uses stored `guardrails.screening`. Add `--recompute --picard-dir /path/to/parabricks_json` to rebuild cycle screening from native tables.

## cfDNA fragmentomics (insert-size)

When `validation.regulatory.primary_analyte` is `cfdna`, the [analyte profile](../../docs/ANALYTE_PROFILES.md) enables cfDNA fragmentomics guardrails automatically (or set `fragmentomics` / `auto_profile_from_analyte` explicitly). Metrics are stored in `fragmentomics_metrics` on each sample JSON. Bisulfite conversion QC is also enabled by default for WGBS analytes.

## Related Documentation

- Theory: [`THEORY.md`](THEORY.md)
- Implementation: [`IMPLEMENTATION.md`](IMPLEMENTATION.md)

## Guardrail Schema Migration

If you already have historical JSON outputs with older guardrail fields (`threshold`, `note`, `diagnose`, `description`, `meaning`, `reason`), migrate them in bulk to the current message-based schema:

- Dry-run (no file writes):
  - `methyl-qc-migrate-guardrails /path/to/alignment_qc_jsons`
- Apply in place:
  - `methyl-qc-migrate-guardrails /path/to/alignment_qc_jsons --apply`
- Apply with per-file backups:
  - `methyl-qc-migrate-guardrails /path/to/alignment_qc_jsons --apply --backup`

## Export JSON Schema

Export the strict, versionable JSON Schema generated from the Pydantic export models:

- `methyl-qc-export-schema` (default: **V1** internal columnar assembly — still includes Picard tables)
- `methyl-qc-export-schema --variant v2` (**V2.1** slim guardrail-summary export)
- `methyl-qc-export-schema --check` — drift check (delegates to the repo-wide exporter when `methyl-validation` is installed)

Default output paths (package-local, kept for backward compatibility):

- V1: `packages/methylalignmentqc/schemas/exported_sample_qc.schema.json`
- V2: `packages/methylalignmentqc/schemas/exported_sample_qc_v2.schema.json`

Canonical copies for all pipeline config models (including these export payloads) also live under **`schemas/config/`** at the repo root (`alignment_qc/exported_sample_qc*.schema.json`). Regenerate everything after model changes:

```bash
source .venv/bin/activate
methyl-export-config-schemas              # all pipeline + AlignmentQC export schemas
methyl-export-config-schemas --check      # fail if artifacts are stale
```

`methyl-qc-export-schema` still writes the package `schemas/` tree and mirrors to `schemas/config/` when the central exporter is available. Use `--output` on `methyl-qc-export-schema` only for ad-hoc paths.

## Slim V2.1 JSON (tools / Azure SQL)

`methyl-qc` writes **V2.1** JSON (`ExportedSampleQCV2Payload`; see `exported_sample_qc_v2.schema.json`). That model is a **disk export**, not `MethylQcTaskInput` / `MethylQcTaskOutput`. Workers bind `sampleDir` + `qcPath` and a compact guardrail/screening summary; Picard tables are read from the sample directory.

**Canonical analysis path:** `guardrails.details` (including nested headings such as `fragmentomics` and sidecar `bisulfite_conversion`). Do not copy `deamination_qscore` under `bisulfite_conversion`.

For **legacy V1** files (columnar arrays, including histograms) or **fat V2.0** files (row-oriented Picard tables), convert in place or to a new tree with:

- Dry-run (no writes):
  - `methyl-qc-convert-v1-to-v2 /path/to/sample.json`
  - `methyl-qc-convert-v1-to-v2 /path/to/json_dir`
- Write V2.1 next to a single file (default name `<stem>.v2.json`):
  - `methyl-qc-convert-v1-to-v2 /path/to/sample.json --apply`
- Write a directory of files into a target folder (each output basename `<stem>.v2.json`):
  - `methyl-qc-convert-v1-to-v2 /path/to/json_dir --apply --output-dir /path/to/v2_out`
- Custom output path (single file):
  - `methyl-qc-convert-v1-to-v2 /path/to/sample.json --apply --output /path/to/out.json`
- If `--output` already exists and you want a backup before overwrite:
  - `... --apply --output /path/to/out.json --backup`
- Skip post-conversion Pydantic validation (not recommended):
  - `... --no-validate-v2`

Slim V2.1 files include top-level `metadata` (`schema_name`, `schema_version` `2.1.0`, `export_kind`, `exported_at_utc`, `producer`) plus summary scalars and `guardrails`. Files that are already slim V2.1 are skipped. Fat V2.0 files are slimmed (histogram keys dropped).

### Azure SQL Database (`json` column)

Assume a table `dbo.sample_qc (sample_id nvarchar(256) NOT NULL, qc_json json NOT NULL)` and a V2.1 payload in `qc_json`. Query **guardrail scalars** with `JSON_VALUE`. Histogram arrays are not on the published export — they remain in Parabricks `{id}.json` / `{id}.qc-metrics.tar`.

```sql
SELECT
  JSON_VALUE(s.qc_json, '$.sample_id') AS sample_id,
  CAST(JSON_VALUE(s.qc_json, '$.summary_stats.duplication_rate') AS float) AS duplication_rate,
  CAST(JSON_VALUE(s.qc_json, '$.guardrails.overall_pass') AS bit) AS overall_pass,
  JSON_VALUE(s.qc_json, '$.guardrails.details.q30_percent.value') AS q30_value,
  JSON_VALUE(s.qc_json, '$.guardrails.details.q30_percent.pass') AS q30_pass,
  JSON_VALUE(s.qc_json, '$.guardrails.details.deamination_qscore.value') AS deamination_qscore,
  JSON_VALUE(s.qc_json, '$.guardrails.details.bisulfite_conversion.conversion_rate_pct.value') AS conversion_rate_pct
FROM dbo.sample_qc AS s;
```

For filtered indexes on extracted scalars, use persisted computed columns that wrap `JSON_VALUE` projections (Azure SQL supports indexed computed columns when deterministic).
