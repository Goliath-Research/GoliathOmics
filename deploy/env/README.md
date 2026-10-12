# Environment templates (`deploy/env/`)

Committed examples for production `/work/goliath/env/`. Copy and edit on the cluster; **do not commit secrets**.

| Template | Target on cluster | Consumers |
|----------|-------------------|-----------|
| `gateway.mssql.env.example` | `/opt/methyl-gateway/env/gateway.env` (gateway VM local disk) | `methyl-gateway`, privileged-host `bootstrap_distributed_workers.sh` |
| `gateway.postgres.env.example` | same | same |
| `worker.env.example` | `/work/goliath/env/worker.env` | `methyl-worker` systemd units |

## Database variables

| Backend | Required env |
|---------|----------------|
| **PostgreSQL** | `BACKEND_DB=postgres`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (or `METHYLPIPELINE_DB`) |
| **Azure SQL** | `BACKEND_DB=mssql`, `AZURE_SQL_SERVER`, `AZURE_SQL_DB`, `AZURE_SQL_USER`, `AZURE_SQL_PASSWORD` (or `METHYLPIPELINE_DB`) |

Do **not** use `PGHOST` / `PGDATABASE` — the Python gateway and Admin CLI read `POSTGRES_*` via [`workflow_engine/rest/connection.py`](../../workflow_engine/rest/connection.py).

## MSSQL base schema

Before incremental deploy scripts, apply the base schema once:

```bash
# Azure SQL — base DDL then incremental
sqlcmd -S "$AZURE_SQL_SERVER" -d "$AZURE_SQL_DB" -U "$AZURE_SQL_USER" -P "$AZURE_SQL_PASSWORD" \
  -i workflow_engine/sql_mssql/MethylPipeline.sql
bash workflow_engine/sql_mssql/deploy_azure.sh
```

PostgreSQL greenfield: `bash workflow_engine/sql_pg/deploy_azure.sh` (includes base objects).

## Related

- [Usage ch.14](../../docs/usage/14-deployment-and-distributed-workflow.md)
- [Production runbook](../../docs/deployment/production_runbook.md)
- [Operator journey](../../docs/deployment/operator-journey.md)
