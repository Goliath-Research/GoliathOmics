# Deployment and Supervision

> Status: proposed regulatory-facing synthesis. Detailed commands remain in
> deployment runbooks and worker protocol documents.

## Deployment Modes

MethylPipeline supports two operational modes that should be documented
separately in regulated use.

| Mode | Purpose | Primary Controls |
|------|---------|------------------|
| Development | Local development, package tests, local workflow smoke runs | Git branch, `.venv`, local fixtures, CI checks |
| Production | Controlled execution on shared `/work` storage with gateway, DB, portal, and remote workers | Versioned release bundle, manifest hashes, worker registration, approved deploy |

Development setup is covered by [`../DEPLOYMENT.md`](../DEPLOYMENT.md) and
usage prerequisites. Production release is covered by
[`../deployment/production_release.md`](../deployment/production_release.md) and
[`../deployment/production_runbook.md`](../deployment/production_runbook.md).

## Production Runtime Topology

Production uses a four-layer runtime:

| Layer | Role |
|-------|------|
| Portal | Study setup, schema-driven configuration editing, workflow start, monitoring |
| Workflow database | Workflow definitions, versions, instances, node executions, leases, output bindings |
| Gateway | Stateless REST API for workers and release/admin operations |
| Workers | Capability-specific task execution on shared storage |

Workers do not connect directly to the database. They poll the gateway, claim
tasks that match registered capabilities, execute package CLIs or in-process
handlers, and submit typed outputs back through the gateway.

Canonical architecture: [`../architecture/distributed-runtime.md`](../architecture/distributed-runtime.md).

## Versioned Release Bundle

Production GPU workers consume versioned releases under `/work/goliath`.
Workers do not require a git checkout at runtime.

Release layout:

```text
/work/goliath/
  current -> releases/<version>
  releases/<version>/
    manifest.json
    requirements-worker.lock
    wheels/
    runtime-bundle/
    methyl-extractor-linux-<arch>.tar.gz
```

The release manifest records the deploy bundle version, component versions,
Python version, Parabricks image digest, lockfile, runtime bundle path, native
artifacts, and SHA256 checksums. Promotion verifies the manifest and checksums
before updating `current`.

Canonical release document: [`../deployment/production_release.md`](../deployment/production_release.md).

## Workflow Definition Deployment

Workflow definitions are compiled from version-controlled DomainPrograms and
deployed to the workflow database as workflow versions. A production instance
therefore binds:

- workflow definition and version,
- compiled graph,
- study context,
- selected profile,
- site configuration,
- action catalog version,
- task schemas.

This makes each workflow instance monitorable and reproducible at the graph
level, not only at the command-line level.

## Worker Registration and Capability Supervision

Workers are registered with identities, tokens, and capabilities. Capability
matching prevents a worker from receiving tasks it cannot execute.

Examples of capability domains:

- sample prep: download, Parabricks, QC, extraction, archive,
- pipeline: centroid, detector, mapper, enricher, progression,
- validation: plan iterations, stability, freeze preparation, model MC,
  model selection, post-model validation.

Each task claim and submission is tied to a workflow node execution and worker.
Long-running tasks can heartbeat during execution. Failed tasks report failure
through the worker protocol and workflow engine.

Canonical worker contract: [`../../workers/WORKER_PROTOCOL.md`](../../workers/WORKER_PROTOCOL.md).

## Portal Supervision

The portal can supervise execution at two levels.

At the workflow level, it can display a Gantt-style execution dashboard:

- workflow instance,
- graph node order and dependencies,
- task state transitions,
- start and finish times,
- active worker or cluster,
- retry/skip/failure state,
- branch result code,
- artifact and report links.

At the action level, it can display worker-submitted outputs:

- typed `output_json`,
- action telemetry,
- result code,
- duration,
- artifact paths,
- manifest paths,
- skip reason and signatures when idempotent replay occurs.

The database stores the queryable execution state; shared `/work` stores rich
action manifests and JSONL timelines for reinspection.

## Idempotency and Replay Control

CLI-backed actions write action result manifests under `.action_results/`.
Before running, the worker can compare the current task input signature against
the stored manifest. When input and output signatures match and artifacts verify,
the worker replays the cached result as a skipped action instead of re-running.

Important controls:

- `input_signature` identifies the task input and resolved configuration.
- `output_signature` identifies output artifacts.
- `action_revision` separates incompatible action implementations.
- `forceRerun` and `METHYL_FORCE_RERUN=1` bypass replay.
- artifact verification prevents false success for actions with required outputs.

This supports restartability without silently accepting stale or incomplete
results.

## Security and Access Controls

The production security model includes:

- TLS edge for gateway access,
- Entra JWT for admin/control-plane routes,
- worker ID and token for worker routes,
- optional cluster CIDR binding,
- managed identity for database access from the gateway,
- database private endpoint where applicable,
- Arc/Defender/Sentinel controls for worker VM supervision.

Operational procedures are documented in
[`../deployment/production_runbook.md`](../deployment/production_runbook.md).

## Rollback and Incident Response

Rollback is performed by promoting a previous release bundle and restarting
workers. Because release manifests pin component versions and hashes, rollback
returns workers to a known artifact set.

Incident response controls include:

- disabling a worker cluster in the workflow database,
- stopping affected worker services,
- revoking or rotating worker tokens,
- requiring clean Arc/Defender status before re-enabling,
- reviewing affected workflow instances and action logs.

## Supervision Evidence To Retain

For each regulated production run, retain:

- release manifest and component hashes,
- workflow version ID and compiled graph hash,
- study manifest and site/profile/program versions,
- workflow instance IDs,
- node execution export,
- worker registration snapshot,
- action result manifests,
- action timeline logs,
- readiness reports,
- model artifacts and validation metrics.
