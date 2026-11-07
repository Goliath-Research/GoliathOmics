-- verify_schema_ledgers.sql — read-only checks for wf.schema_release and wf.omics_schema_release
-- Usage (after connect vars are set):
--   psql -v ON_ERROR_STOP=1 -f docs/examples/sql/verify_schema_ledgers.sql
--
-- Does not modify data.

\set ON_ERROR_STOP on

SELECT 'wf.schema_release' AS ledger,
       count(*)::bigint AS rows
FROM wf.schema_release;

SELECT release,
       goliathapp_sha,
       apply_count,
       applied_at_utc
FROM wf.schema_release
ORDER BY applied_at_utc DESC
LIMIT 5;

SELECT 'wf.omics_schema_release' AS ledger,
       count(*)::bigint AS rows
FROM wf.omics_schema_release;

SELECT release,
       omics_sha,
       apply_count,
       applied_at_utc
FROM wf.omics_schema_release
ORDER BY applied_at_utc DESC
LIMIT 5;
