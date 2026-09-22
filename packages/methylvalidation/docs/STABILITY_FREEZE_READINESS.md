# Stability and freeze readiness (pre-model)

After Monte Carlo **stability** and production **freeze**, review artifacts before **`--model`** so the frozen DMP panel, mapped-feature cache, and progression summaries support the intended disease narrative.

## What this checks

| Area | Inputs | Notes |
|------|--------|--------|
| **Enricher** | `production/enricher/enricher_completeness.json`, per-comparison `enrich_*.csv` | All libraries from preset; warn or no_go if incomplete |
| **Stability** | `monte_carlo_runs/stability/stability_summary.json`, `stable_dmps_production.csv`, `dmp_frequency.csv` | Run counts, recurrence threshold, panel non-empty |
| **Freeze** | `monte_carlo_runs/production/production_summary.json`, `project.json`, `stable_dmps_genomewide.csv` | Step RCs, `fixed_dmp_panel`, legacy detector keys, mapper annotation cache metadata |
| **Progression** | `monte_carlo_runs/production/progression/summary.json`, `modules_long.csv`, `entities_progression_labels.csv` | Stage order, missing inputs, module score vs stage trend |
| **Balance** | Stable panel or frequency CSV | Chromosome concentration warnings |

Set `actionConfig.validation.require_complete_enricher: true` to treat incomplete Enrichr as **no_go** (default: warning only).

**Important:** MC **stability** does not run disease progression; progression is produced during **freeze** (and downstream mapper/enricher) when `actionConfig.progression.enabled` is true. See [`USAGE.md`](USAGE.md).
For observed-hybrid mapped-family model builds (`feature_family_set != dmp_scored`), freeze should also emit mapper annotation cache metadata (`production_summary.json -> mapper_annotation_cache`) and a cache CSV under `production/model_bundle/`.

## CLI

From the repo root (venv active):

```bash
methyl-stability-freeze-readiness /path/to/<project_name>
```

`<project_name>` is the directory that **contains** `monte_carlo_runs/` (e.g. `/work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG`). Do **not** pass a `*.json` config path — the CLI requires the **folder** (otherwise you get `no_go` with missing artifacts or exit **2** if that path is an existing file).

**Outputs (default):** JSON and markdown are written next to your Monte Carlo tree under **` <project_name>/readiness/readiness.json`** and **` <project_name>/readiness/readiness.md`**. Markdown is also printed to stdout. Override destinations with **`--json-out`** / **`--markdown-out`** (absolute paths are used as-is; **relative paths are resolved under `readiness/`**, not the shell cwd). Use **`--stdout-only`** to skip writing files (pipe-friendly).

Optional **`--redact-paths`** omits `project_root`, filesystem artifact paths, and similar fields from **exported** JSON and markdown while retaining deterministic verdict content.

### Grok advisory review (xAI)

By default the CLI asks **Grok** (`grok-4.3`) for **non-blocking**, advisory commentary on progression/module summaries vs optional disease context. This never overrides the deterministic **go / go_with_risks / no_go** verdict or exit codes.

| Behavior | Default |
|---------|---------|
| Grok call | **On** — disable with `--no-grok-review` |
| Model | `grok-4.3` (`--grok-model`) |
| Evidence rows in AI payload | Top `--grok-max-top-rows` (default **10**) module trends + label counts |
| HTTP | `--grok-timeout-seconds 60`, `--grok-max-retries 2`, `--grok-temperature 0.1` |

**Credential resolution** follows **MethylMapper** [`SecureCredentialManager`](../../../packages/methylmapper/methyl_mapper/secure_credentials.py), in this order: **`--grok-api-key`**, encrypted file (**default** `~/.methyl_mapper/credentials/grok_api_key.encrypted`; override with **`--encrypted-file-path`** to match `methyl-mapper`), optional Azure KV (**`--azure-key-vault-url`**, **`--azure-secret-name`**), then **`GROK_API_KEY`**. Use **`--methyl-mapper-home`** if your mapper config root is not `~/.methyl_mapper`.

If resolution fails, **`ai_review.status`** is **`skipped_no_key`** and **`credential_hint`** explains why (missing file, decrypt error, unset env). Common causes: running readiness under a **different user or host** than where `methyl_mapper_credentials save` wrote the file; **decrypt mismatch** — if you saved with **`METHYL_MAPPER_CREDENTIAL_PASSWORD`**, export the same variable before running readiness; or **`methyl-validation`** installed without **`methyl_mapper`** (then encrypted files are never read — reinstall with mapper available).

If no key is found or the API errors, the tool still emits **`ai_review`** and continues — readiness verdict stays unchanged.

**Privacy:** the Grok payload is built without project roots or artifact paths; free-text fields (e.g. verdict warnings) are scrubbed for obvious `/home/…`, `/work/…`, etc. **`--include-ai-raw-response`** adds truncated raw model text to `ai_review` (default off). Use **`--redact-paths`** when sharing exported markdown/JSON outside trusted hosts.

`production/project.json` **`actionConfig.mapper.disease_term`** is passed through as **`disease_context`** unless **`--disease-context`** is set.

`production/project.json` **`actionConfig.validation.regulatory.primary_analyte`** is also passed into the Grok advisory payload as analyte context (alongside `sample_type`). This helps interpretation stay aligned with analyte biology (for example, buffy-coat host-response signatures vs cfDNA tumor-derived signatures). If `primary_analyte` is not declared, readiness still runs and marks analyte framing as not declared.

For **cfDNA** projects, readiness also summarizes **fragmentomics**:

- Phase 1: `alignment_qc/*.json` → `fragmentomics_metrics` from insert-size histograms (`actionConfig.alignment_qc.fragmentomics` or `auto_profile_from_analyte` when `primary_analyte` is `cfdna`).
- Phase 2 (optional): `fragmentomics/fragmentomics_summary.json` from `methyl-fragmentomics` when `actionConfig.fragmentomics.enabled` is true.

Warnings are emitted when `primary_analyte=cfdna` but these artifacts are missing or guardrails fail.

Plasma/cfDNA **retrain** workflow: see [PLASMA_RETRAIN_PATH.md](PLASMA_RETRAIN_PATH.md). Set `regulatory.model_training_analyte` to `cfdna` and optionally `enforce_training_analyte_match: true` so `--model` cannot reuse a buffy-coat `locked_model_spec.json`.

When the production project JSON is valid for [`ProjectConfig`](../../../packages/methylutils/methyl_utils/pipeline_config.py), optional **`description`** fields on disease **`GroupConfig`** entries (including each nested **`stages[]`** child) are read and sent to Grok as **`ordered_stage_narratives`** (comparison token, `disease_group`, short text — no `sample_paths`). The markdown report also lists them under **Stage definitions (from project config)** when present.

Exit codes:

- `0` — overall verdict is `go` or `go_with_risks`
- `2` — overall verdict is `no_go`

## Verdicts

- **`go`** — stability and freeze checks passed; progression present and parsable without blocking gaps (warnings may still apply).
- **`go_with_risks`** — non-blocking warnings (e.g. weak module trajectory, chromosome imbalance, missing progression).
- **`no_go`** — missing stability/freeze artifacts, empty stable panel, failed freeze steps, or forbidden legacy detector keys in `production/project.json`.

Human biological review remains required before treating outputs as validated hypotheses.

## JSON output (module trajectory)

Field `progression.module_trajectory.median_abs_pearson_stage_vs_score` is the median of **absolute Pearson** correlations between stage order (comparison labels mapped to `0..K-1`) and per-module scores from `modules_long.csv`. It does **not** use Spearman rank correlation; use scipy/R separately if you need rank-based association.

Exported JSON may also include **`ai_review`** (Grok status, parsed structured commentary, optional raw excerpt) and **`ai_review_payload_meta`** when Grok is enabled — purely advisory.

## Example output

A report generated for `Healthy_vs_PCa1-4-CG` (when that tree exists on your machine) is checked in as:

- [`examples/stability_freeze_readiness_Healthy_vs_PCa1-4-CG.md`](examples/stability_freeze_readiness_Healthy_vs_PCa1-4-CG.md)

## Implementation

[`methyl_validation/stability_freeze_readiness.py`](../methyl_validation/stability_freeze_readiness.py)
