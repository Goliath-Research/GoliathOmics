# Regulatory and Product Controls

!!! warning "Claim boundary"
    Architecture, profiles, and these synthesis documents are **not** FDA clearance
    or a filed SaMD submission. Live study/model claims belong in the
    [validation evidence index](validation-evidence-index.md) after pivotal runs.

This folder is the regulatory-facing synthesis layer for MethylPipeline. It
does not replace the theory, usage, implementation, architecture, deployment,
or reference documentation. Instead, it explains how those materials combine
into a controlled software product with traceable configuration, deployment,
execution, validation evidence, and change management.

## Audience

Use these documents for product review, quality-system planning, FDA-style
submission preparation, internal design control, and release readiness
discussions.

| Document | Purpose |
|----------|---------|
| [Regulatory-Ready Platform for Multiomics Diagnostics](Regulatory-Ready%20Platform%20for%20Multiomics%20Diagnostics.md) | Product positioning, open-core packaging, GTM, and analyte/process roadmap (strategy). Standalone HTML (offline Mermaid; repo-only, not in the MkDocs site): `docs/regulatory/Regulatory-Ready Platform for Multiomics Diagnostics.html` — regenerate with `scripts/render_regulatory_standalone.sh`. Sales pitch deck (Marp; repo-only): `docs/presentations/regulatory-ready-platform-multiomics.md` (HTML/PDF via `scripts/render_presentations.sh`). |
| [Product and operational controls](methylpipeline-product-and-operational-controls.md) | Product scope, feature inventory, configuration-as-workflow model, and control summary |
| [Deployment and supervision](deployment-and-supervision.md) | Production topology, worker supervision, portal monitoring, release bundles, rollback, and security controls |
| [Validation evidence index](validation-evidence-index.md) | Template and registry structure for real study/model results tied to exact software and configuration versions |
| [SaMD submission scaffold](samd-submission-scaffold.md) | 510(k)/De Novo-style content map → existing controls (not a filed submission) |
| [Change management plan](change-management-plan.md) | Proposed change classification, impact assessment, CI gates, release controls, and model revalidation triggers |
| [Continuous integration and regression testing](continuous-integration-and-regression-testing.md) | Regression gate, test taxonomy, coverage measurement, per-package test expectation, and CI evidence |
| [Traceability matrix](traceability-matrix.md) | Mapping from product claims and controls to source docs, schemas, tests, and runtime evidence |

## Operational study path (SaMD ladder)

For creating new healthy-vs-disease studies with real holdouts and a small profile progression
(`samd_research` → `samd_holdout_enrichment` → `samd_pivotal`), use the operator SOP:

- [`../usage/18-samd-study-lifecycle.md`](../usage/18-samd-study-lifecycle.md)
- Scaffold: `methyl-study-init` / validate: `methyl-study-validate-manifest`
- Example manifests (repo-only; not in the MkDocs site): `docs/examples/samd/` — see also [application packs (ch.24)](../usage/24-methylation-application-packs.md)

## Relationship to Canonical Docs

| Pillar | Canonical Location | Regulatory Use |
|--------|--------------------|----------------|
| Theory | [`../theory/`](../theory/index.md) | Statistical method, assumptions, validation limitations |
| Usage | [`../usage/`](../usage/index.md) | Operator run paths, artifact gates, troubleshooting |
| Architecture | [`../architecture/`](../architecture/index.md) | System topology and configuration layers |
| Implementation | [`../implementation/`](../implementation/index.md) | Engine, compiler, workers, package boundaries |
| Deployment | [`../deployment/`](../deployment/production_runbook.md) | Production release and runtime operations |
| Reference | [`../reference/`](../reference/schema-index.md) | JSON Schemas, action contracts, DomainProgram language |

## Maintenance Rule

These documents should cite canonical sources for detailed behavior and should
be updated when any change affects intended use, workflow topology, schema
contracts, production deployment, worker traceability, release controls,
regression testing, or validation evidence.
