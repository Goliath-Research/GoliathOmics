# Environment templates (`deploy/env/`)

Committed examples for production `/work/goliath/env/`. Copy and edit on the cluster; **do not commit secrets**.

| Template | Target on cluster | Consumers |
|----------|-------------------|-----------|
| `gateway.postgres.env.example` | `/opt/methyl-gateway/env/gateway.env` (gateway VM local disk) | `methyl-gateway`, privileged-host `bootstrap_distributed_workers.sh` |
| `worker.env.example` | `/work/goliath/env/worker.env` | `methyl-worker` systemd units |

## Database variables

| Backend | Required env |
|---------|----------------|
| **PostgreSQL** | `BACKEND_DB=postgres`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (or `METHYLPIPELINE_DB`) |

Do **not** use `PGHOST` / `PGDATABASE` — the Python gateway and Admin CLI read `POSTGRES_*` via [`workflow_engine/rest/connection.py`](../../workflow_engine/rest/connection.py).

## PostgreSQL schema

GoliathApp engine DDL is applied from a release bundle (`scripts/apply_goliathapp_schema.sh`) or from a GoliathApp checkout (`workflow_engine/sql_pg/apply_schema.sh`). Omics test-bed seeds use `workflow_engine/sql_pg/deploy_test_bed.sh` after the engine schema is present.

## Related

- [Usage ch.14](../../docs/usage/14-deployment-and-distributed-workflow.md)
- [Production runbook](../../docs/deployment/production_runbook.md)
- [Operator journey](../../docs/deployment/operator-journey.md)
