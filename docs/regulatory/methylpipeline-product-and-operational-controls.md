# MethylPipeline Product and Operational Controls

!!! warning "Claim boundary"
    This synthesis summarizes implemented controls for design review. It is
    **not** a filed SaMD submission or FDA clearance. Evidence packages belong in
    the [validation evidence index](validation-evidence-index.md).

> Status: proposed regulatory-facing synthesis. This document summarizes
> implemented product controls and points to canonical engineering documents
> for detail.

## Purpose

MethylPipeline is both a methylation-analysis product and a workflow execution
platform. It supports study configuration, sample preparation, quality control,
DMP discovery, biological interpretation, model creation, prediction, distributed
execution, release management, and audit-oriented observability.

For FDA-style review, the product should be presented as a controlled system:
software version, study definition, site definition, workflow program, pipeline
profile, task payloads, worker actions, model artifacts, and validation results
must all be traceable to one another.

## Feature Inventory

### Scientific Pipeline Features

| Feature Area | Capability | Canonical Docs |
|--------------|------------|----------------|
| Sample preparation | FASTQ ingress, Parabricks alignment, remediation, methylation extraction, archive and cleanup | [`../usage/03-sample-prep-and-qc.md`](../usage/03-sample-prep-and-qc.md), [`../../workflow_engine/docs/portal_study_lifecycle.md`](../../workflow_engine/docs/portal_study_lifecycle.md) |
| Quality control | Alignment QC, extraction QC, guardrails, branchable remediation decisions | [`../usage/10-artifacts-and-qa-checks.md`](../usage/10-artifacts-and-qa-checks.md) |
| DMP discovery | Centroid construction, detector, Storey FDR, biological effect ranking, fixed panel mode | [`../theory/chapters/02-methylcentroid.md`](../theory/chapters/02-methylcentroid.md), [`../theory/chapters/03-methyldetector.md`](../theory/chapters/03-methyldetector.md) |
| Stability and freeze | Monte Carlo recurrence, stable panel, production freeze, readiness checks | [`../theory/chapters/12-two-workflows.md`](../theory/chapters/12-two-workflows.md), [`../../packages/methylvalidation/docs/STABILITY_FREEZE_READINESS.md`](../../packages/methylvalidation/docs/STABILITY_FREEZE_READINESS.md) |
| Interpretation | DMP to gene mapping, feature weights, enrichment, PPI, disease progression | [`../theory/chapters/07-methylmapper.md`](../theory/chapters/07-methylmapper.md), [`../theory/chapters/08-methylenricher.md`](../theory/chapters/08-methylenricher.md) |
| Modeling and prediction | ECDF classifier, alternative model backends, predictor, holdout evaluation | [`../theory/chapters/04-methylclassifier.md`](../theory/chapters/04-methylclassifier.md), [`../theory/chapters/15-model-creation-and-validation.md`](../theory/chapters/15-model-creation-and-validation.md) |

### Platform Features

| Feature Area | Capability | Canonical Docs |
|--------------|------------|----------------|
| Workflow engine | DomainProgram compilation, graph scheduling, local and DB-backed execution | [`../implementation/workflow-engine.md`](../implementation/workflow-engine.md), [`../reference/domain-program-language.md`](../reference/domain-program-language.md) |
| Distributed runtime | Portal, database, stateless gateway, remote workers, shared storage | [`../architecture/distributed-runtime.md`](../architecture/distributed-runtime.md) |
| Worker execution | Capability-based polling, leases, heartbeats, typed outputs, action result manifests | [`../../workers/WORKER_PROTOCOL.md`](../../workers/WORKER_PROTOCOL.md) |
| Configuration contracts | JSON Schema, Pydantic task I/O, action catalog, config boundary checks | [`../reference/schema-index.md`](../reference/schema-index.md), [`../reference/action-parameter-contract.md`](../reference/action-parameter-contract.md) |
| Storage accounts | DB SoT (`cfg.storage_endpoint` / `cfg.credential`); portal admin authoring; workers receive secrets via gateway only; node-local credential cache | [`../architecture/config-registry.md`](../architecture/config-registry.md), [`../deployment/portal_resource_profile.md`](../deployment/portal_resource_profile.md) |
| CI/CD and release | PR checks, versioned wheels, runtime bundle, manifest hashes, gated deployment | [`../../ci/README.md`](../../ci/README.md), [`../deployment/production_release.md`](../deployment/production_release.md) |

## Configuration-As-Workflow Model

MethylPipeline separates workflow intent from code. Operators and workflow
authors describe studies, sites, profiles, and programs in validated JSON
artifacts. Code resolves and validates those artifacts, then executes the
resulting graph.

| Layer | Artifact | Control Role |
|-------|----------|--------------|
| Study manifest | `project_*.json` | Cohorts, sample paths, comparisons, chromosomes, validation partitions |
| Site manifest | `/work/site/methyl_site.json` | Reference genomes, GTFs, caches, cluster/site defaults |
| Pipeline profile | `*.profile.json` | Reusable `actionConfig` packs and procedure-level defaults |
| DomainProgram | `*.program.json` | Workflow topology: loops, branches, parallel blocks, actions |
| Instance context | `context_json` | Frozen run bindings, planned iterations, selected profile, resolved paths |
| Storage registry | `cfg.storage_endpoint` + `cfg.credential` | Lab/infra-admin authored locations and secrets (portal → DB); expanded into task JSON at schedule |
| Task payload | `input_json`, `resolvedConfig`, `resolvedProject` | Worker-executable action input and merged action parameters |

The canonical layer model is [`../architecture/layer-model.md`](../architecture/layer-model.md).
The highest-level rule is config-not-code: tunable science and operational
parameters must be resolved from schema-validated configuration, not hidden in
Python defaults.

## Portal and Gantt-Style Supervision

Because DomainPrograms compile to explicit workflow nodes, edges, conditions,
FOREACH scopes, and action bindings, a workflow instance can be supervised as a
time-ordered execution plan.

The portal can expose:

- workflow instance status,
- node status: pending, ready, leased, running, completed, failed, skipped,
- worker assignment and capability,
- start, finish, and duration per node,
- branch result codes,
- typed input and output summaries,
- artifact links,
- retry and idempotent skip status,
- downstream readiness gates.

This supports a Gantt-style dashboard for the full instance while preserving
worker-level evidence for each action.

## Worker-Level Auditability

Each worker action is traceable through three complementary records:

| Record | Location | Purpose |
|--------|----------|---------|
| DB `node_execution.output_json` | Workflow database | Portal/API query, branch bindings, typed action result |
| `.action_results/*.json` | Shared `/work` output tree | Re-readable execution manifest, artifact inventory, signatures |
| `action_run_log.jsonl` / `sample_prep_log.jsonl` | Shared `/work` output tree | Append-only action and sample timelines |

Action result manifests include telemetry and idempotency fields such as
`action_revision`, `input_signature`, `output_signature`, `skipped`, and
`skip_reason`. See [`../../workers/WORKER_PROTOCOL.md`](../../workers/WORKER_PROTOCOL.md).

## Evidence Boundary

Architecture, deployment controls, and traceability are necessary but not
sufficient for FDA approval. Each intended clinical claim requires real
study-level evidence tied to exact:

- release bundle version,
- source tags and component versions,
- runtime bundle contents,
- study manifest,
- site manifest,
- profile,
- DomainProgram,
- workflow instance IDs,
- model artifacts,
- validation partitions,
- performance metrics and confidence intervals.

Use [`validation-evidence-index.md`](validation-evidence-index.md) to register
that evidence.
