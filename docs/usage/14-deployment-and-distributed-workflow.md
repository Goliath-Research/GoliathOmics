# Deployment and Distributed Workflow

## Purpose

Deploy the **control plane** (database, REST gateway) and **GPU workers** that execute SamplePrep and validation workflows. This chapter consolidates the dual-backend story (Azure SQL and PostgreSQL) and links to detailed runbooks.

**Canonical production platform:** [`docs/deployment/production-platform.md`](../deployment/production-platform.md)  
**Deep dive:** [`docs/deployment/production_runbook.md`](../deployment/production_runbook.md)  
**Operator journey (day-1 → day-N):** [`docs/deployment/operator-journey.md`](../deployment/operator-journey.md)  
**Worker protocol:** [`workers/WORKER_PROTOCOL.md`](../../workers/WORKER_PROTOCOL.md)  
**REST contract:** [`contracts/openapi.yaml`](../../contracts/openapi.yaml)

## Architecture

```mermaid
flowchart TB
  subgraph portal ["Company Portal"]
    editor["Schema config editor Web :8077"]
    runPrep["Start SamplePrepPipeline"]
    runDdp["Start DataDrivenPipeline"]
  end
  subgraph database ["Backend Database"]
    wfPrep["SamplePrepPipeline"]
    wfDef["DataDrivenPipeline"]
    inst["workflow_instance + context_json"]
    nexec["node_execution + scope_variable"]
  end
  subgraph mt ["Middle-Tier"]
    rest["WfEngine REST :8080"]
    engine["Engine procs T-SQL / PL/pgSQL"]
  end
  subgraph workers ["Remote Workers"]
    w0["download / Parabricks / QC / extract"]
    w1["methyl-centroid / detector"]
    w3["mapper / enricher / progression"]
  end
  storage[("Shared storage NFS / Azure Files /work/...")]

  editor --> runPrep
  runPrep --> wfPrep
  wfPrep -->|"HDF5 ready"| runDdp
  runDdp --> wfDef
  wfDef --> inst
  inst --> nexec
  rest --> engine
  engine --> nexec
  w0 --> rest
  w1 --> rest
  w3 --> rest
  w0 --> storage
  w1 --> storage
  w3 --> storage
  nexec -.->|"input paths"| storage
```

*Distributed runtime*



| Component | Role |
|-----------|------|
| **Database** | Stores workflow definitions, instances, scope, task queue |
| **Portal SQL** (`portal.sp_*`) | Middle-tier orchestration; starts instances; **preregisters worker IPs** |
| **Gateway** (`methyl-gateway`) | Worker-only REST (`poll` / `submit` / `enroll`) — no catalog admin HTTP |
| **Workers** (`methyl-worker`) | Execute capabilities (Parabricks, methyl-qc, detector, …); Arc Connected |
| **`/work` storage** | Shared FASTQs, BAMs, HDF5, MC outputs, `/work/goliath` release |

The portal does **not** call the gateway. Catalog and DomainProgram deploy use **direct DB** scripts on a privileged host. Workers enroll and claim over HTTPS with a token; production gateways set `GATEWAY_REQUIRE_ARC_ATTEST=1`.

## Greenfield checklist

Use this ordered checklist once per environment (detail: [production-platform.md](../deployment/production-platform.md)):

1. [ ] Deploy database schema + Python populate on a **privileged host** (`deploy_azure.sh` twins, then `bootstrap_distributed_workers.sh`)
2. [ ] Install the gateway on a **gateway VM local disk** (`provision_gateway_node.sh` — no `/work` mount)
3. [ ] Enable nginx TLS; set `GATEWAY_REQUIRE_ARC_ATTEST=1`; `https://<fqdn>/v1/health` returns 200
4. [ ] Portal-preregister each GPU public IP
5. [ ] First GPU worker seeds shared `/work` (layout, release, extractor, one Parabricks pull), then enrolls
6. [ ] Later GPU workers: VM-local host/Docker, then enroll (`provision_worker_node.sh`)
7. [ ] Smoke test: `scripts/smoke_sample_prep.sh`, `scripts/smoke_study_lifecycle.sh`

## Database deploy (dual-backend)

Both backends implement the same **`wf`** schema contract. Choose one primary backend per environment; CI and local dev often use PostgreSQL.

### PostgreSQL (schema twin / CI; canonical DB `goliath`)

| Step | Command / doc |
|------|----------------|
| Schema scripts | [`workflow_engine/sql_pg/README.md`](../../workflow_engine/sql_pg/README.md) |
| Automated deploy | `./workflow_engine/sql_pg/deploy_azure.sh` (core engine + portal DDL) |
| Cluster IP binding | Optional: `wf_cluster_security_columns.sql` (run manually when using IP-bound clusters) |
| FOREACH support | Included in `08_foreach_support.sql` |

```bash
export BACKEND_DB=postgres
export POSTGRES_HOST=your-server.postgres.database.azure.com
export POSTGRES_PORT=5432
export POSTGRES_DB=goliath
export POSTGRES_USER=dba
export POSTGRES_PASSWORD='...'
./workflow_engine/sql_pg/deploy_azure.sh

# Optional: cluster IP binding columns (when using wf.cluster security features)
psql -f workflow_engine/sql_pg/wf_cluster_security_columns.sql
```

### Azure SQL (production portal)

| Step | Command / doc |
|------|----------------|
| Script order | [`workflow_engine/README.md`](../../workflow_engine/README.md) |
| Bundled schema | `workflow_engine/sql_mssql/MethylPipeline.sql` |
| FOREACH | `wf_sql_foreach_support.sql` (both backends) |

```bash
# Example: sqlcmd against Azure SQL with scripts 1–18 from workflow_engine/README.md
```

**Note:** `deploy_azure.sh` applies core engine scripts plus portal DDL (`portal_resource_profile.sql`, `portal_workflow_api.sql`, `wf_drop_platform_sample_storage.sql`). Run `wf_cluster_security_columns.sql` separately when enabling cluster IP binding.

## Action catalog and workflow definitions

After schema deploy:

```bash
source .venv/bin/activate
methyl-export-task-schemas
methyl-export-action-catalog
python workflow_engine/sql_mssql/seed_action_catalog.py

# Deploy SamplePrep + StudyValidation DomainPrograms (direct DB):
export BACKEND_DB=mssql   # or postgres
# AZURE_SQL_* or POSTGRES_*
bash scripts/deploy_workflow_definitions.sh
```

Output: `workflow_versions.json` with `workflow_version_id` values for portal / CI start.

Canonical programs (repo):

- SamplePrep: [`workflow_engine/domain/fixtures/sample_prep.program.json`](../../workflow_engine/domain/fixtures/sample_prep.program.json)
- Study validation: check-specific `*.program.json` under `workflow_engine/domain/checks/`

Authoring language: [`docs/reference/domain-program-language.md`](../reference/domain-program-language.md)

## Gateway configuration

Install from repo root:

```bash
source .venv/bin/activate
pip install -e workflow_engine/
```

Copy and edit an example env file:

| Backend | Template |
|---------|----------|
| PostgreSQL | [`deploy/env/gateway.postgres.env.example`](../../deploy/env/gateway.postgres.env.example) |
| Azure SQL | [`deploy/env/gateway.mssql.env.example`](../../deploy/env/gateway.mssql.env.example) |

Install systemd + nginx with [`scripts/provision_gateway_node.sh`](../../scripts/provision_gateway_node.sh) (`--root /opt/methyl-gateway`, not `/work`). Unit template: [`deploy/systemd/methyl-gateway.service`](../../deploy/systemd/methyl-gateway.service). Nginx: [`deploy/nginx/methyl-gateway.conf`](../../deploy/nginx/methyl-gateway.conf).

Key variables (see `workflow_engine/rest/connection.py`):

- `BACKEND_DB` — `postgres` or `mssql`
- `POSTGRES_*` or `AZURE_SQL_*` — connection targets
- `WF_USE_MANAGED_IDENTITY` — Azure Entra token auth for DB
- Worker auth is `worker_id` + `worker_token` on `/v1/workers/*` (gateway is worker-only)

## Worker configuration

Each GPU worker runs one or more `methyl-worker` systemd units (see [`deploy/systemd/methyl-worker@.service`](../../deploy/systemd/methyl-worker@.service)).

| Variable | Purpose |
|----------|---------|
| `WORKER_ID` | Unique worker name |
| `WORKER_TOKEN` | Auth token from **gateway enroll** (`/etc/methyl/worker-token`) |
| `WORKER_CAPABILITY` | e.g. `parabricks.fq2bam`, `methyl-qc`, `pipeline.detector` |
| `WORKER_API_BASE` | Gateway URL (`https://gateway.example.com/v1`) |

Enrollment and provisioning (production = portal IP prereg + Arc + `methyl-worker enroll`):

- [`docs/deployment/production-platform.md`](../deployment/production-platform.md)
- [`docs/deployment/worker_provision.md`](../deployment/worker_provision.md)
- [`docs/deployment/arc_worker_runbook.md`](../deployment/arc_worker_runbook.md)
- `scripts/provision_worker_node.sh`, `methyl-worker enroll`

Worker poll/submit contract: [`workers/WORKER_PROTOCOL.md`](../../workers/WORKER_PROTOCOL.md)

## End-to-end operator flow

### 1. SamplePrepPipeline

Prefer portal SQL (`portal.sp_create_and_start_instance`) after planning context. For CI:

```bash
methyl-study-start sample-prep-start request.json
```

See [Sample Prep and Quality Control](03-sample-prep-and-qc.md) for QC gates.

### 2. Study validation / MC

After SamplePrep **COMPLETED**, start validation via portal SQL or:

```bash
methyl-workflow-run \
  --program workflow_engine/domain/checks/.../configs/your_mc.program.json \
  --context-file workflow_engine/domain/profiles/your.profile.json \
  --context '{"projectPath":"/work/.../configs/project.json"}'
```

Or legacy CLI recovery: [Stage: Stability](05-stage-stability.md) (transitional `methyl-validation` flags).

### 3. Local workflow (no gateway)

For development without database:

```bash
methyl-workflow-run --program path/to/program.json \
  --context-file profile.json \
  --context '{"projectPath":"/work/.../project.json"}' \
  --stub-external
```

## Related documentation

- Production platform: [`docs/deployment/production-platform.md`](../deployment/production-platform.md)
- Production runbook: [`docs/deployment/production_runbook.md`](../deployment/production_runbook.md)
- Release layout: [`docs/deployment/production_release.md`](../deployment/production_release.md)
- Portal lifecycle: [`workflow_engine/docs/portal_study_lifecycle.md`](../../workflow_engine/docs/portal_study_lifecycle.md)
- SamplePrep operator guide: [`workflow_engine/sql_mssql/SamplePrepFlow.md`](../../workflow_engine/sql_mssql/SamplePrepFlow.md)
- Documentation audit: [`docs/DOCUMENTATION_AUDIT.md`](../DOCUMENTATION_AUDIT.md)
