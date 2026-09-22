# Change Management Plan

!!! warning "Claim boundary"
    This is a proposed product-control document, not an approved QMS SOP and not
    FDA clearance. Align with the organization's formal quality system before
    treating it as official procedure.

> Status: proposed product control. Align this with the organization's formal
> quality management system before using it as an official SOP.

## Purpose

This document defines a proposed change-management structure for MethylPipeline
as a regulated software product. It connects source control, CI/CD, schemas,
workflow definitions, deployment bundles, and model validation evidence.

## Change Classes

| Class | Examples | Typical Impact |
|-------|----------|----------------|
| Documentation only | Clarifications, link fixes, non-procedural notes | No software behavior change |
| Infrastructure | CI pipeline, deployment script, VM setup, gateway config | May affect release or operations |
| Schema or contract | Pydantic model, JSON Schema, OpenAPI, action catalog | May affect portal editing, worker payloads, DB payloads |
| Workflow topology | DomainProgram nodes, branches, FOREACH scopes, bindings | May affect execution order and dashboard plan |
| Scientific method | Detector, classifier, validation, mapper/enricher behavior | May affect results and model claims |
| Configuration profile | Profile/site `actionConfig` values or defaults | May affect outputs without code changes |
| Security | Authentication, tokens, managed identity, network restrictions | May affect access and compliance posture |
| Model artifact | Frozen panel, classifier, predictor, calibration, backend choice | Directly affects predictions |

## Source Control Controls

Recommended controls:

- all production code changes occur through reviewed pull requests,
- release builds originate from signed or protected tags where available,
- release tags use SemVer-compatible identifiers,
- generated schemas and action catalogs are committed with model changes,
- docs are updated in the same change when behavior, deployment, or operator
  instructions change,
- production deployment uses approved release bundles, not ad hoc checkout
  execution on workers.

## Impact Assessment Checklist

For each change, answer:

- Does it change an action input or output model?
- Does it require regenerating JSON Schemas?
- Does it change `schemas/actions/catalog.json`?
- Does it change a DomainProgram or compiled workflow graph?
- Does it change study/site/profile configuration semantics?
- Does it affect a tunable science parameter?
- Does it affect worker idempotency signatures or artifact manifests?
- Does it affect portal display or Gantt-style workflow status?
- Does it affect release packaging, deployment, rollback, or worker setup?
- Does it affect model outputs, metrics, calibration, or biological ranking?
- Does it require re-running validation evidence packages?

## Required Gates By Change Type

Every non-documentation change additionally runs the **full regression suite**
(all packages) with coverage reporting as a pull-request gate. See
[`continuous-integration-and-regression-testing.md`](continuous-integration-and-regression-testing.md).

| Change Type | Required Gates |
|-------------|----------------|
| Documentation only | Link check, doc freshness where applicable; regression suite still runs |
| Python package code | Full regression suite passes, coverage report reviewed, import checks, relevant package tests |
| Task I/O model | `methyl-export-task-schemas --check`, schema review |
| Action catalog | `methyl-export-action-catalog --check`, DB seed/redeploy plan |
| Config model | config schema export/check, config boundary check |
| DomainProgram | domain schema export/check, compile smoke, workflow deploy plan |
| Worker protocol | worker tests, OpenAPI/schema review, protocol docs update |
| Deployment scripts | shell syntax, packaging smoke, release runbook update |
| Scientific behavior | full regression suite, targeted new/updated tests, validation artifact review, possible model revalidation |
| New feature | new tests demonstrating the feature and guarding against future regression |
| Security | threat/permission review, deployment runbook update, verification step |

Current CI hooks and documentation checks are summarized in
[`../DOCUMENTATION_AUDIT.md`](../DOCUMENTATION_AUDIT.md) and [`../../ci/README.md`](../../ci/README.md).

## Schema and Contract Change Policy

When changing a workflow-exposed action boundary:

1. Update the Pydantic input/output model.
2. Keep `extra="forbid"` unless there is a documented compatibility reason.
3. Regenerate task schemas.
4. Regenerate action catalog if the action metadata changed.
5. Run boundary checks.
6. Update reference documentation.
7. Confirm portal/editor compatibility when the schema is operator-facing.

Workflow workers must consume materialized task payloads and `resolvedConfig`.
They must not re-read study manifests, profiles, or environment variables for
tool parameters when a resolved task configuration is present.

## Configuration Change Policy

Study manifests describe cohorts, comparisons, paths, chromosomes, regulatory
metadata, and validation partitions. They must not carry tool parameters.

Tunable action parameters belong in:

- site `actionConfig`,
- profile `actionConfig`,
- instance/program override where explicitly intended.

Changing a profile or site parameter can change scientific outputs even without
code changes. For regulated use, such changes should receive impact assessment
and may require a new evidence package.

## Workflow Topology Change Policy

DomainProgram changes can affect:

- node order,
- parallelism,
- branch behavior,
- FOREACH scope expansion,
- task bindings,
- output variable propagation,
- portal dashboard shape.

Any production DomainProgram change should be compiled, schema-validated,
deployed as a new workflow version, and tied to release/evidence records.

## Release and Deployment Change Policy

The standard production release path is:

1. Tag MethylExtractor when native code changes.
2. Tag MethylPipeline when Python, worker, schema, or runtime-bundle content changes.
3. Run release assembly with explicit component version pins.
4. Generate a release manifest with checksums.
5. Deploy through the gated production pipeline.
6. Promote to `/work/goliath/current`.
7. Restart or roll workers according to the runbook.

Rollback promotes a previous release bundle and restarts workers. A rollback
should be recorded with reason, affected instances, and verification results.

## Model Change and Revalidation Policy

Treat the following as a new model version:

- changed stable DMP panel,
- changed production freeze inputs,
- changed classifier or predictor backend,
- changed training cohort or validation partition,
- changed profile/site parameters that affect detector, classifier, predictor,
  mapper, enrichment, or model selection,
- changed software behavior that affects model features or probabilities.

Revalidation expectations should scale with impact:

| Change | Suggested Revalidation |
|--------|------------------------|
| Documentation only | No model revalidation |
| Deployment script only | Deployment smoke and release verification |
| Schema-only compatible addition | Schema checks and portal/editor verification |
| Action output shape | Worker/protocol tests and portal verification |
| Scientific parameter profile | Re-run affected validation stage or full evidence package |
| Detector/classifier/predictor behavior | Re-run model creation and holdout evidence |
| Training cohort or intended-use claim | New or amended validation evidence package |

## Deviations and Emergency Changes

Emergency fixes should still produce:

- linked issue or incident record,
- affected release/version,
- risk assessment,
- minimal verification evidence,
- rollback plan,
- post hoc review,
- documentation update if operator behavior changed.

## Change Record Template

```markdown
## Change Record: <title>

- Change ID:
- Date:
- Author:
- Reviewer(s):
- Class:
- Affected release:
- Affected workflow/action/config:
- Summary:
- Rationale:
- Impact assessment:
- Required gates:
- Test evidence:
- Schema/catalog changes:
- Deployment impact:
- Model/evidence impact:
- Rollback plan:
- Approval:
```
