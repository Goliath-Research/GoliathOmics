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

## Sequencing runs and alignment results

`22_omics_run_alignment_objects.sql` records workflow facts as Meta objects:

- **`omics/SequencingRun` (parent `platform/Batch`).** One object per succeeded `sample.download_fastq` step. Fields: sample, study, FASTQ files, run accession and ingest time.
- **`omics/AlignmentResult` (parent `platform/Document`).** One object per succeeded alignment step (`sample.parabricks_fq2bam` for Clara Parabricks or GoliathAlign, `sample.parabricks_rna_fq2bam`, `sample.parabricks_giraffe`, `sample.docker_align`, `sample.methylgrapher_wgbs_align`). It links to its run, sample and study, and records the BAM, output directory, QC metrics file and completion time.

`omics_clinical.wf_meta_object_source` lists the source actions. `omics_clinical.wf_meta_object_link` keeps one row per source node execution and class, so each writer is idempotent per wf row.

The writers are SQL class methods (`RecordFromNodeExecution`, `Backfill`). They run from an AFTER trigger on `wf.node_execution` when a source step succeeds. A writer error is reported as a warning and never fails the engine. Such a row stays in `omics_clinical.v_run_alignment_pending`.

Objects are written with `"Meta".sp_obj_insert` / `sp_obj_update`, so `"Meta"."ObjValuesHistory"` (GoliathApp H2) records every change.

For rows that existed before the trigger, run the official backfill. It only derives from real rows and is a no-op when rerun:

```bash
./scripts/omics_run_alignment_backfill.sh            # dry run: what would be created
./scripts/omics_run_alignment_backfill.sh --apply
```
