# GoliathOmics domain PostgreSQL schema

Canonical ordered DDL for the omics child app (clinical portal tables, cfg analyte catalog, and related APIs).

Apply after GoliathApp platform schema:

```bash
export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...
./scripts/apply_domain_schema.sh
```

Release bundles still ship `omics-sql/sql_pg/*.sql` under legacy filenames (symlinks from `workflow_engine/sql_pg/`). Apply order is defined in `scripts/domain_schema_order.sh`.

One-off migrations under `workflow_engine/sql_pg/migrations/` are not part of the apply list.
