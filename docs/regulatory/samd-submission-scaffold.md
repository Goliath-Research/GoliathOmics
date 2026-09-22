# SaMD submission scaffold (510(k) / De Novo map)

> **Disclaimer:** This document is an internal **documentation scaffold**. It maps
> FDA-style submission topics to existing MethylPipeline controls and evidence.
> It is **not** a filed 510(k) or De Novo, and does **not** constitute FDA
> clearance or approval. Architecture, profiles, and filled feasibility evidence
> packages ≠ regulatory authorization.

## Purpose

Help quality and clinical teams assemble a submission narrative by pointing each
content area at canonical repo artifacts and on-disk study evidence.

Operator study path: [`../usage/18-samd-study-lifecycle.md`](../usage/18-samd-study-lifecycle.md).  
Evidence registry: [`validation-evidence-index.md`](validation-evidence-index.md).

## Claim gate (code-enforced)

| Requirement | Where enforced |
|-------------|----------------|
| `regulatory.stage` must be `pivotal_validation` (or later) before clinical performance claims | `RegulatoryLifecycleConfig` in `packages/methylvalidation/methyl_validation/config.py` |
| `allow_clinical_performance_claims: true` forbidden on earlier stages | same |
| Real holdouts for enrichment/pivotal profiles | `methyl-study-validate-manifest` + study `validation_partitions` |

Current prostate packages in the evidence index are **`feasibility`** with **no partitions** — they must not be cited as pivotal clinical performance.

## Content map (scaffold)

| Submission topic (illustrative) | MethylPipeline control / artifact |
|---------------------------------|-----------------------------------|
| Device description / intended use | Study `regulatory.intended_use_summary`, `claim_boundary`, analyte; SaMD SOP ch.18 |
| Software architecture / SoTA | [`../architecture/index.md`](../architecture/index.md), [`methylpipeline-product-and-operational-controls.md`](methylpipeline-product-and-operational-controls.md) |
| Design controls / change control | [`change-management-plan.md`](change-management-plan.md), [`traceability-matrix.md`](traceability-matrix.md) |
| Configuration management | Four-layer config (site / profile / study / program); `config-not-code`; CAAS / `hyperparamSetId` |
| Verification (unit/integration) | [`continuous-integration-and-regression-testing.md`](continuous-integration-and-regression-testing.md), CI JUnit |
| Deployment / cybersecurity | [`deployment-and-supervision.md`](deployment-and-supervision.md), worker/gateway env templates |
| Analytical validation (stability, freeze) | `monte_carlo_runs/stability/`, freeze readiness AR, production `locked_model_spec.json` |
| Clinical performance (when allowed) | `clinical_performance_report.json` (Wilson/LCB); WF3 holdout eval; **requires pivotal stage + partitions** |
| PCCP / post-market | `pccp_draft.*`, `post_market_monitoring_scaffold.*` from `regulatory_artifacts.py` (draft scaffolds) |
| Traceability of a specific result | Evidence package in [`validation-evidence-index.md`](validation-evidence-index.md) |

## Prostate feasibility packages (starting points)

| ID | Study | Use in a draft submission narrative |
|----|-------|-------------------------------------|
| EV-PCA-PLASMA-2026-06 | Plasma_healthy_vs_PCa | Richest chain (stability → production → PMV clinical reports); still feasibility |
| EV-PCA-HGOOD-2026-06 | H_PCa_good | Freeze/model present; gene panel empty at threshold — document limitation |
| EV-PCA-BUFFY-2026-07 | Buffy_healthy_vs_PCa | Stability + readiness only; incomplete model chain |

## SaMD profile ladder

Operator profiles on the claim path (see also [ch.18](../usage/18-samd-study-lifecycle.md)):

| Profile | Role |
|---------|------|
| `samd_research` | Discovery / feasibility; research-mode overlays when folded |
| `samd_holdout_enrichment` | Locked holdout enrichment; WF3 on `locked_test` |
| `samd_pivotal` | Pivotal validation; clinical performance claims only after stage + review |

## Recommended path to claim-ready evidence

1. Scaffold study with `methyl-study-init`; assign patient-disjoint `locked_test` early.
2. Research with `samd_research` (+ research mode overlay when folded); lock HPs.
3. Enrichment with `samd_holdout_enrichment`; WF3 on `locked_test`.
4. Open `pivotal_validation` cohort; run `samd_pivotal`; set stage + claims only after review.
5. Fill a new evidence package (not feasibility drafts) with release SHA, CI, and partition IDs.

## Related

- [`README.md`](README.md) — regulatory folder index
- [`../usage/18-samd-study-lifecycle.md`](../usage/18-samd-study-lifecycle.md)
- Theory WF2 vs WF3: [`../theory/chapters/12-two-workflows.md`](../theory/chapters/12-two-workflows.md)
