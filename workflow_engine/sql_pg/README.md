# Legacy sql_pg layout (deprecation)

**Canonical domain DDL:** [`../../database/schema/`](../../database/schema/) applied via [`../../scripts/apply_domain_schema.sh`](../../scripts/apply_domain_schema.sh).

Files listed in `apply_schema.sh` `SCRIPTS=` are symlinks into `database/schema/` for one release cycle so `omics-sql/` bundles keep legacy paths (`sql_pg/portal_clinical_schema.sql`, …).

Test-bed seeds, cfg reference asset seeds, and one-off migrations stay in this directory and are **not** part of the domain apply list.

Operator scripts (not applied by `apply_schema.sh`):

- [`enqueue_sample_prep_test_start.sql`](enqueue_sample_prep_test_start.sql) — queue a SamplePrep test start on `cfg.study_start_request` via `portal.sp_request_study_start` (see [`docs/operators/start-sample-prep.md`](../../docs/operators/start-sample-prep.md)).
