# Validation Evidence Index

> Status: template and registry structure. Populate this file with real
> study/model entries as validation runs are completed and reviewed.

## Purpose

This index ties real results to exact software, configuration, workflow, data,
and model artifacts. It is the regulatory bridge between product controls and
clinical or analytical performance claims.

**When to add a package:** after a `samd_pivotal` (or equivalent) run that used a
populated `pivotal_validation` partition, freeze readiness go/go_with_risks, and
a reviewed intended-use statement. Operator path:
[`../usage/18-samd-study-lifecycle.md`](../usage/18-samd-study-lifecycle.md).
Example partition shapes (repo-only): `docs/examples/samd/` — see [ch.18](../usage/18-samd-study-lifecycle.md) and [ch.24](../usage/24-methylation-application-packs.md).

Architecture and release controls show that the product is controlled. This
index shows what evidence was produced by a specific controlled version.

## Evidence Principles

Each evidence package should be:

- version-bound: exact release bundle and component versions,
- configuration-bound: exact study, site, profile, and DomainProgram inputs,
- instance-bound: workflow instance IDs and node execution exports,
- data-bound: cohort/sample IDs and validation partitions,
- artifact-bound: model, panel, readiness, prediction, and metric outputs,
- limitation-aware: caveats and residual risks documented with the result.

## Evidence Package Template

Copy this template for each reviewed study/model.

```markdown
## Evidence Package: <Study / Model Name>

### Administrative Metadata

- Evidence package ID:
- Review status: draft | under review | approved | superseded
- Prepared by:
- Reviewers:
- Date:
- Intended use / claim:

### Software and Release

- MethylPipeline release bundle:
- MethylPipeline component version:
- MethylExtractor component version:
- Runtime bundle path:
- Release `manifest.json` path:
- Release manifest SHA256:
- Git tag / commit:
- CI run:
- Regression test results (JUnit):
- Coverage report:
- Real reference-sample(s) used (id / analyte / provenance):
- Real-data test run (JUnit):
- Deploy approval record:

### Configuration Inputs

- Study manifest path:
- Study manifest SHA256:
- Site manifest path:
- Site manifest SHA256:
- Pipeline profile:
- Pipeline profile SHA256:
- DomainProgram path:
- DomainProgram SHA256:
- Workflow version ID:
- Action catalog version/hash:
- JSON Schema export version/hash:

### Data and Cohorts

- Project/study:
- Analyte:
- Species/genome build:
- Control cohort:
- Disease cohort(s):
- Total samples:
- Exclusion criteria:
- QC pass/fail summary:
- Validation partitions:
  - development_train:
  - locked_test:
  - pivotal_validation:
  - post_market_monitoring:

### Workflow Instances

- SamplePrepPipeline instance ID:
- StudyValidationLifecycle instance ID:
- Holdout or blind prediction instance ID:
- Node execution export:
- Worker/action timeline export:
- Portal dashboard snapshot/export:

### Model Creation Artifacts

- Monte Carlo run root:
- Stability summary:
- Stable DMP panel:
- Production freeze project:
- Production summary:
- Biological readiness report:
- Classifier artifacts:
- Predictor artifacts:
- Model bundle:

### Validation Results

- Internal stability metrics:
- Frozen-model predictor metrics:
- True holdout metrics:
- Bootstrap confidence intervals:
- Blind prediction report:
- Calibration metrics:
- Confusion matrix:
- ROC-AUC / macro AUC:
- Sensitivity / specificity:
- Balanced accuracy:

### Traceability Artifacts

- Action result manifests:
- `action_run_log.jsonl`:
- `sample_prep_log.jsonl`:
- Node execution outputs:
- Idempotency signature records:
- Artifact hash manifest:

### Deviations and Limitations

- Known deviations:
- Failed or retried tasks:
- Missing artifacts:
- Data limitations:
- Statistical limitations:
- External validation status:
- Residual risk:

### Decision

- Verdict: accepted | accepted with risks | rejected | superseded
- Rationale:
- Required follow-up:
```

## Current Evidence Packages

| Evidence Package | Product Release | Study / Claim | Status | Primary Result Location |
|------------------|-----------------|---------------|--------|-------------------------|
| EV-PCA-PLASMA-2026-06 | `/work/goliath/current` manifest `2026.06.1` | Plasma healthy vs PCa (cfDNA); **feasibility only** | draft — engineering/feasibility | `/work/projects/prostate-cancer/Plasma_healthy_vs_PCa/` |
| EV-PCA-HGOOD-2026-06 | `/work/goliath/current` manifest `2026.06.1` | H_PCa_good buffy; **feasibility only** | draft — engineering/feasibility | `/work/projects/prostate-cancer/H_PCa_good/` |
| EV-PCA-BUFFY-2026-07 | `/work/goliath/current` manifest `2026.06.1` | Buffy healthy vs PCa; **feasibility only** | draft — incomplete model chain | `/work/projects/prostate-cancer/Buffy_healthy_vs_PCa/` |

> **Claim boundary:** All three packages are **feasibility / expanded-development engineering evidence**.
> Study manifests have `regulatory.stage: feasibility`, **no** `validation_partitions`, and
> `allow_clinical_performance_claims` must remain false. They are **not** pivotal clinical
> performance evidence. See [`samd-submission-scaffold.md`](samd-submission-scaffold.md).
>
> **Profile provenance:** New packages should record both `pipelineProfile` and `researchMode`
> (e.g. `samd_research` + `dual_fc`). Legacy `mc_*` names fold into those modes; historical
> runs below may only list the MC snapshot path.

---

## Evidence Package: Plasma_healthy_vs_PCa (EV-PCA-PLASMA-2026-06)

### Administrative Metadata

- Evidence package ID: `EV-PCA-PLASMA-2026-06`
- Review status: draft
- Date: 2026-07-10 (index fill from on-disk artifacts)
- Intended use / claim: Research-use cfDNA methylation healthy vs prostate cancer discrimination — **feasibility only; no clinical performance claim**

### Software and Release

- MethylPipeline release bundle: `/work/goliath/current/` (manifest version `2026.06.1`)
- Release `manifest.json` path: `/work/goliath/current/manifest.json`
- Release manifest SHA256: TBD (operator to record at package approval)
- Git tag / commit: TBD
- CI run / JUnit / coverage: TBD

### Configuration Inputs

- Study manifest path: `/work/projects/prostate-cancer/configs/project_Plasma_healthy_vs_PCa.json`
- Site manifest path: `/work/site/methyl_site.json` (typical)
- Pipeline / MC snapshot: `/work/projects/prostate-cancer/Plasma_healthy_vs_PCa/monte_carlo_runs/queue/mc_config.json` (`n_iterations: 10`)
- DomainProgram: historical MC stability / freeze / model path (not SaMD ladder)

### Data and Cohorts

- Project/study: `Plasma_healthy_vs_PCa`
- Analyte: `cfdna` (`regulatory.stage: feasibility`)
- Validation partitions: **none declared** (`locked_test` / `pivotal_validation` empty/absent)

### Model Creation Artifacts

- Monte Carlo run root: `/work/projects/prostate-cancer/Plasma_healthy_vs_PCa/monte_carlo_runs/`
- Stability summary: `…/stability/stability_summary.json` (10 runs; ~84 649 stable DMPs @ freq≥0.7; ~23 339 stable genes @ freq≥0.5; discovery/enricher axes)
- Stable panels: `…/stability/stable_dmps_production.csv`, `…/stability/stable_genes_production.csv`
- Freeze readiness AR: `…/.action_results/validation_stability_freeze_readiness.default.json` (`result_code: 0`)
- Production: `…/monte_carlo_runs/production/` (`locked_model_spec.json`, `model_bundle/`, `pccp_draft.json`, `post_market_monitoring_scaffold.json`)

### Validation Results

- Internal stability: see `stability_summary.json`
- Post-model / clinical reports: `…/monte_carlo_runs/post_model_validation/` (10× `clinical_performance_report.json`)
- True holdout (WF3 `locked_test` / `pivotal_validation`): **not run** — partitions undeclared

### Deviations and Limitations

- Feasibility stage; no patient-disjoint holdout partitions
- Post-model reports are frozen-model MC evaluation, not classical pivotal holdout
- Release SHA / CI binding TBD at approval

### Decision

- Verdict: accepted with risks (engineering/feasibility registry only)
- Required follow-up: declare partitions; promote via `samd_holdout_enrichment` / `samd_pivotal` before any clinical claim

---

## Evidence Package: H_PCa_good (EV-PCA-HGOOD-2026-06)

### Administrative Metadata

- Evidence package ID: `EV-PCA-HGOOD-2026-06`
- Review status: draft
- Date: 2026-07-10
- Intended use / claim: Buffy-coat healthy vs PCa (good cohort) — **feasibility only**

### Software and Release

- Same release root as Plasma (`2026.06.1`); SHA/CI TBD

### Configuration Inputs

- Study manifest: `/work/projects/prostate-cancer/configs/project_H_PCa_good.json`
- MC snapshot: `…/H_PCa_good/monte_carlo_runs/queue/mc_config.json` (`n_iterations: 10`)
- Analyte: `buffy_coat`; `regulatory.stage: feasibility`; **no partitions**

### Model Creation Artifacts

- Stability: `…/H_PCa_good/monte_carlo_runs/stability/stability_summary.json` (classifier DMP/gene axes; **5** runs analyzed for DMP panel ~3 824 stable DMPs; **gene_stable_at_threshold: 0** with BA skips)
- Freeze readiness AR: `…/.action_results/validation_stability_freeze_readiness.default.json` (`result_code: 0`)
- Production: `…/monte_carlo_runs/production/` (`locked_model_spec.json`, `production_summary.json`, `model_bundle/`, `pccp_draft.*`, `post_market_monitoring_scaffold.*`)
- Post-model validation dir: **absent**

### Deviations and Limitations

- Empty stable gene panel at threshold; gene-axis BA filtering skipped many panels
- No true holdout partitions; feasibility only

### Decision

- Verdict: accepted with risks (feasibility registry)
- Required follow-up: gene-axis review; partitions; SaMD ladder before claims

---

## Evidence Package: Buffy_healthy_vs_PCa (EV-PCA-BUFFY-2026-07)

### Administrative Metadata

- Evidence package ID: `EV-PCA-BUFFY-2026-07`
- Review status: draft
- Date: 2026-07-10
- Intended use / claim: Buffy healthy vs PCa — **feasibility; incomplete freeze→model chain**

### Software and Release

- Same release root (`2026.06.1`); SHA/CI TBD

### Configuration Inputs

- Study manifest: `/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json`
- **Migrated research context (2026-07-10):** `/work/projects/prostate-cancer/configs/context_samd_research_buffy.json`
  - `pipelineProfile: samd_research`, `researchMode: dual_fc`
  - DomainProgram: `fixtures/mc_stability.program.json` (DB names `MC_Stability` / `SaMD_Research`)
- Legacy overlay `buffy_mc_gene_fc.json` deprecated (see sibling `.DEPRECATED.md`)
- MC snapshot (historical run): `…/Buffy_healthy_vs_PCa/monte_carlo_runs/queue/mc_config.json` (`n_iterations: 10`, prior `raw_pool`/`none`)
- Analyte: `buffy_coat`; manifest `regulatory.stage: expanded_development` with empty `validation_partitions` stubs; **no holdout IDs yet**

### Model Creation Artifacts

- Stability: `…/stability/stability_summary.json` (10 runs; ~79 646 stable DMPs; ~22 647 stable genes; discovery/enricher axes)
- Freeze readiness AR: present (`result_code: 0`)
- Production / locked model / PCCP under `monte_carlo_runs/production/`: **absent**
- Post-model validation: **absent**

### Deviations and Limitations

- Stability + freeze-readiness only; no production freeze/model bundle on disk for this package
- Not suitable even as internal model-claim evidence until freeze→model completes

### Decision

- Verdict: accepted with risks (stability-only registry entry)
- Required follow-up: complete freeze/model; partitions; SaMD ladder

---

## Gap list (ops follow-up)

1. Populate `validation_partitions` on live prostate manifests (patient-disjoint `locked_test`, later `pivotal_validation`).
2. Re-run enrichment/pivotal tiers with `samd_holdout_enrichment` / `samd_pivotal` when cohorts allow.
3. Bind release manifest SHA256 + CI JUnit into each package at approval.
4. Complete Buffy freeze→model chain before elevating that package.

## Minimum Evidence Set For A Model Claim

Before a model claim is presented externally, the evidence package should include
at minimum:

- release manifest and deploy record,
- frozen study/profile/program/site inputs,
- sample prep and QC summary,
- stability summary and stable panel,
- production freeze summary,
- biological readiness report,
- final classifier and predictor artifacts,
- true holdout evaluation when available,
- bootstrap confidence intervals for primary metrics,
- node execution export and worker action timeline,
- documented limitations and decision rationale.

## Real Results vs Demonstrations

Smoke runs, synthetic fixtures, and development demos are useful engineering
evidence but should not be presented as clinical or analytical validation. They
belong in CI and development-readiness records.

Regulatory evidence requires real cohorts, declared partitions, locked outputs,
and traceability to the deployed product version that produced the results.

## Related Documents

- [`../theory/chapters/12-two-workflows.md`](../theory/chapters/12-two-workflows.md)
- [`../theory/chapters/15-model-creation-and-validation.md`](../theory/chapters/15-model-creation-and-validation.md)
- [`../usage/10-artifacts-and-qa-checks.md`](../usage/10-artifacts-and-qa-checks.md)
- [`deployment-and-supervision.md`](deployment-and-supervision.md)
- [`traceability-matrix.md`](traceability-matrix.md)
