# Deployment and Distributed Workflow

The gateway, engine DDL, and `goliath-cfg` live in GoliathApp. This repository supplies science seeds, DomainPrograms, and `methyl_worker` handlers. `methyl-cfg` remains the genomics CLI alias for one release cycle. Dependency direction: [architecture/dependencies.md](../architecture/dependencies.md).

The operator procedure for release **2026.10.27** is [Release, deploy, and worker enrollment](../operators/release.md). Profiles are in [Deploy targets](../deploy-targets.md). Rollouts service ids are in [rollouts.md](../rollouts.md).

## Purpose

Deploy the database, the REST gateway, and the workers that execute SamplePrep and validation. One primary database per environment. The goliath-test profile uses Neon Postgres.

## Architecture

```mermaid
flowchart TB
  subgraph portal ["Portal"]
    runPrep["Queue SamplePrep start"]
  end
  subgraph database ["Workflow database"]
    graph["workflow version and nodes"]
    inst["workflow_instance + context_json"]
  end
  subgraph mt ["Gateway host"]
    rest["methyl-gateway on 127.0.0.1:8080"]
    nginx["nginx /v1/"]
  end
  subgraph workers ["Workers"]
    w0["download, align, QC, extract, delete_bam"]
  end
  storage[("Shared /work")]
  scratch[("Worker METHYL_LOCAL_TMP")]

  runPrep --> inst
  graph --> inst
  nginx --> rest
  rest --> database
  w0 --> nginx
  w0 --> storage
  w0 --> scratch
```

*Distributed runtime. The BAM for a sample stays on that worker's scratch. FASTQs and published H5 files use `/work`.*

| Component | Role |
|-----------|------|
| **Database** | Workflow definitions, instances, scope, task queue. GoliathApp `sql_pg` plus this repo's `cfg_analyte_catalog.sql`. |
| **Portal SQL** (`portal.sp_*`) | Starts instances from `cfg.study_start_request`. Preregisters worker IPs with `portal.sp_upsert_worker_enrollment`. |
| **Gateway** (`methyl-gateway`) | Worker REST under `/v1/workers/*`: enroll, session open, task request, submit. nginx proxies public 443 to loopback `8080`. |
| **Workers** (`methyl-worker`) | Claim tasks. Align through `delete_bam` is pinned to one worker. |
| **`/work`** | Shared FASTQs, published H5, study outputs, `/work/goliath` releases. Not the BAM disk. |

The portal does not call the gateway. Catalog and DomainProgram deploy use direct database scripts. Workers enroll and claim over HTTPS with a token.

## What to run

Follow [Release](../operators/release.md). In order:

1. Push `v<semver>` so **GoliathOmics release** publishes `methyl-pipeline-release-<semver>.tar.gz`.
2. Run **GoliathOmics release assemble** so `goliathomics-<releaseVersion>` exists.
3. Run **GoliathOmics release deploy** with `target` set to a profile (`goliath-test`) or left empty for a runner-local promote.
4. Preregister each worker, then let `provision_worker_node.sh --enroll-worker` call `POST {WORKER_API_BASE}/workers/enroll`.
5. Load process packs with `scripts/deploy_process_pack_catalog.sh --backend postgres --sync` when the release schema is applied. See [Process packs](../operators/process-packs.md).
6. Compile DomainPrograms into database nodes with `scripts/deploy_workflow_definitions.sh`.

GoliathApp's `workflow_engine/sql_pg/apply_schema.sh`, `scripts/bootstrap_distributed_workers.sh`, `contracts/openapi.yaml`, and `workers/WORKER_PROTOCOL.md` are not in this repository. `pip install -e workflow_engine/` does not install the gateway: this checkout has no `workflow_engine/pyproject.toml`. The gateway wheel is produced by `scripts/build_gateway_wheel.sh` from a GoliathApp checkout.

## Gateway configuration

`scripts/deploy_target.sh --component gateway` writes `<install_root>/env/gateway.env` and runs `scripts/install_gateway_systemd.sh` and `scripts/setup_gateway_nginx.sh`. The unit file is [`deploy/systemd/methyl-gateway.service`](../../deploy/systemd/methyl-gateway.service). The nginx site is [`deploy/nginx/methyl-gateway.conf`](../../deploy/nginx/methyl-gateway.conf).

Env template: [`deploy/env/gateway.postgres.env.example`](../../deploy/env/gateway.postgres.env.example).

Connection variables the gateway and `methyl-study-start` read:

- `BACKEND_DB` — `postgres`
- `POSTGRES_*`
- `WF_GATEWAY_HOST` / `WF_GATEWAY_PORT` — loopback bind, default `127.0.0.1:8080`
- `WF_USE_MANAGED_IDENTITY` — Azure Entra token auth for the database, when the gateway build supports it

Worker auth is enroll (identity token in `WORKER_TOKEN`), `POST /v1/workers/session` for a bearer session, then task calls with `Authorization: Bearer`, `X-Goliath-Worker-Token`, and `worker_id` in the JSON body.

## Worker configuration

Each worker runs `methyl-worker` (unit [`deploy/systemd/methyl-worker.service`](../../deploy/systemd/methyl-worker.service), or the templated `methyl-worker@.service` when that file is present).

| Variable | Purpose |
|----------|---------|
| `WORKER_ID` | Numeric id returned by enroll. Written to `/etc/methyl/worker-token`. |
| `WORKER_TOKEN` | Token returned by enroll. Same file, mode 0600. |
| `WORKER_API_BASE` | Gateway URL ending in `/v1`, for example the profile `gateway.public_base_url`. |
| `WORKER_CAPABILITY` | Optional capability pin in the shared `worker.env`. Enroll sends the probed capability list in the JSON body. |
| `METHYL_LOCAL_TMP` | Scratch root for BAM and extract. Default `$TMPDIR/methyl-pb` or `/tmp/methyl-pb`. |

`scripts/write_worker_env.sh` writes the shared `/work/goliath/env/worker.env` without credentials. Do not put `WORKER_ID` or `WORKER_TOKEN` in that shared file.

Enrollment command and the preregistration SQL are in [Release](../operators/release.md#worker-enrollment).

## Workflow definitions

```bash
source .venv/bin/activate
methyl-export-task-schemas
methyl-export-action-catalog
bash scripts/deploy_workflow_definitions.sh
```

`deploy_workflow_definitions.sh` compiles SamplePrep, the remediate program, the study-validation lifecycle, and the RNA-seq and proteomics programs when those fixtures exist. It stores each graph with `wf.wf_repo_create_workflow_graph`. `--dry-run` writes the compiled JSON and does not deploy. `--keep-instances` is the default. The script needs `BACKEND_DB=postgres` and `POSTGRES_*`.

Programs live in [`workflow_engine/domain/fixtures/`](../../workflow_engine/domain/fixtures/).

## End-to-end operator flow

### 1. SamplePrepPipeline

```bash
methyl-study-start sample-prep-start request.json
```

The request shape, the four align paths, the scratch pin, and the QC window are in [Start a SamplePrep instance](../operators/start-sample-prep.md). QC artifact rules are in [Sample prep and QC](03-sample-prep-and-qc.md).

### 2. Study validation

After SamplePrep completes, queue a validation start or run the lifecycle program with `methyl-workflow-run` from the gateway wheel. See [Orchestration](04-orchestration-workflow-run.md).

### 3. Local compile

```bash
python scripts/compile_workflow_program.py \
  workflow_engine/domain/fixtures/sample_prep.program.json \
  -o /tmp/sample_prep_compiled.json
```

That script imports `domain.compiler` from the methyl-gateway wheel.

## Related documentation

- [Release, deploy, and enrollment](../operators/release.md)
- [Deploy targets](../deploy-targets.md)
- [Rollouts](../rollouts.md)
- [Process packs](../operators/process-packs.md)
- [SamplePrep storage contract](../../workflow_engine/contract/SamplePrepFlow.md)
