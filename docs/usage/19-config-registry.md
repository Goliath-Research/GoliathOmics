# Config registry

The registry engine and `goliath-cfg` live in GoliathApp. This chapter is the genomics path: analyte JSON, profiles, assay procedures, and handlers stay in GoliathOmics. `methyl-cfg` is the one-cycle alias of `goliath-cfg` and is installed with the methyl-gateway wheel. Pack layout: [Process packs](../operators/process-packs.md).

## What this chapter is

The database owns sites, profiles, procedures, studies, storage endpoints, and reference assets. `finalize_instance_context` syncs the published study onto `/work` when an instance starts. Production storage and credential rows are written through portal SQL. The sync script below is the git-to-database path for analytes, profiles, and procedures.

## Load the catalog

PostgreSQL, after the release schema is on the database:

```bash
export GOLIATH_RELEASE_SRC=/work/goliath/current
# or GOLIATHAPP_SQL_DIR pointing at a GoliathApp workflow_engine/sql_pg checkout
./scripts/deploy_process_pack_catalog.sh --backend postgres --sync
```

The shell script applies GoliathApp `cfg_schema.sql`, `cfg_repo_api.sql`, `cfg_process_pack_catalog.sql`, `cfg_assay_procedure_links.sql`, and `portal_contract_api.sql` when that directory is set. It then applies this repository's [`workflow_engine/sql_pg/cfg_analyte_catalog.sql`](../../workflow_engine/sql_pg/cfg_analyte_catalog.sql). `--sync` runs:

```bash
python scripts/sync_cfg_profiles_and_action_catalog.py --backend postgres --skip-seed
```

That upserts `cfg.analyte` from `workflow_engine/domain/analytes/`, then `cfg.pipeline_profile` and `cfg.assay_procedure` from `workflow_engine/domain/profiles/` and `procedures/`. Each document's `catalog` block maps to cfg `status` (`published` or `retired`). Overlays under `profiles/modes/` are not profile rows. Assay bind sets `analyte_id` when the analyte row exists. Studies with `regulatory.primary_analyte` get `default_analyte_id` backfilled. Omit `--skip-seed` to also re-seed `wf.workflow_action` from `schemas/actions/catalog.json` and `schemas/tasks/`.

GoliathApp-owned DDL (`cfg_repo_api.sql`, process-pack catalog, portal contract) ships in the release `goliathapp-sql/` bundle (script order from GoliathApp `workflow_engine/sql_pg/apply_schema.sh`). This repository adds [`cfg_analyte_catalog.sql`](../../workflow_engine/sql_pg/cfg_analyte_catalog.sql).

DomainProgram graphs are a separate step: `scripts/deploy_workflow_definitions.sh`. See [Deployment](14-deployment-and-distributed-workflow.md).

## Study scaffold

`python workflow_engine/admin/study_init.py` writes a study tree and can upsert `cfg.study` when a cfg store is available. The release venv may expose the same module as `methyl-study-init`. This repository does not register that console script in `pyproject.toml`.

## Credentials

**Production:** EpiPortal admins (`portal.sp_upsert_credential` / `sp_upsert_storage_endpoint` + publish). Never put AccessKeys / SAS URLs in project JSON under `/work/projects`.

**Development / CI:** `methyl-cfg upsert credential` / `storage_endpoint` against `METHYL_CFG_STORE` or DB seed is allowed for bootstrap. Materialize writes **redacted** endpoint metadata only.

Expand at schedule/study-start binds secrets into task `input_json` with `contentHash`. Workers cache node-locally; they never read SQL.

## Study membership

**Starting an instance syncs** cfg onto `/work` (`ensure_study_work_synced` inside `finalize_instance_context`). Sample membership for SamplePrep is the request `samples` list, `sampleCsv`, or `useProjectSamples`. See [Start a SamplePrep instance](../operators/start-sample-prep.md).
