# GoliathOmics domain PostgreSQL schema

Canonical ordered DDL for the omics child app (clinical portal tables, cfg analyte catalog, and related APIs).

Apply after GoliathApp platform schema:

```bash
export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...
./scripts/apply_domain_schema.sh
```

Release bundles still ship `omics-sql/sql_pg/*.sql` under legacy filenames (symlinks from `workflow_engine/sql_pg/`). Apply order is defined in `scripts/domain_schema_order.sh`.

One-off migrations under `workflow_engine/sql_pg/migrations/` are not part of the apply list.

## Clinical record history

`20_omics_clinical_record_history.sql` keeps a history row for every insert, update and delete on
the clinical tables (`portal."Patients"`, `"Samples"`, `"Institutions"` and the other tables from
`01_portal_clinical_schema.sql`, plus every `omics_clinical.*` table) in
`omics_clinical.record_history`: row key, before and after documents, acting portal user and
session (when GoliathApp's session resolve sets `goliath.actor_user_id` / `goliath.actor_session_id`),
database role and transaction id. Updates that change nothing are not recorded. The first apply
records existing rows as `baseline` entries.

With GoliathApp's history ledger installed (`portal.sp_ledger_register`), the table is registered
as the ledger `omics_clinical.record_history`: append-only, hash-chained and checked by
`portal.fn_ledger_verify_all()`. Without it the table is append-only through its own guard; re-run
the file after updating GoliathApp to register the ledger and chain the rows already recorded.
