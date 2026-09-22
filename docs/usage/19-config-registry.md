# Config registry (`methyl-cfg`)

## What this chapter is

Operator path for the **configuration registry**: database (or file-backed store) owns sites, profiles, DomainPrograms, studies, storage endpoints/credentials, and reference assets; `methyl-cfg materialize` writes non-secret files onto `/work` for cluster workers. **Production storage/credential authoring** is EpiPortal → `portal.sp_*` (lab/infra admins); `methyl-cfg upsert` is for **dev/CI/bootstrap** only.

Architecture detail: [`../architecture/config-registry.md`](../architecture/config-registry.md).

## Bootstrap

```bash
source .venv/bin/activate
bash scripts/bootstrap_distributed_workers.sh
# privileged host: deploy_azure.sh + Python process-pack / catalog / workflow populate
```

## Day-2 operations

```bash
export METHYL_CFG_STORE=/work/goliath/cfg-store
export PYTHONPATH=workflow_engine:$PYTHONPATH

# After editing a profile in the store
methyl-cfg materialize --work-root /work

# Publish a DomainProgram (compile + write fixtures under runtime-bundle)
methyl-cfg publish-program StudyValidationLifecycle --work-root /work

# Dev/CI: rotate cloud keys via methyl-cfg (production uses portal.sp_upsert_credential)
methyl-cfg upsert credential --file new_keys.json --name lab-aws-keys --version 2 --provider s3 --publish
```

### Push git profiles + action catalog into the databases

`methyl-cfg import-fs` updates the **file-backed** cfg store. After breaking profile/schema changes, also refresh the **database** registry and `wf` action catalog on each backend:

```bash
source .venv/bin/activate
# Azure SQL (AZURE_SQL_* or DB_*); PostgreSQL (POSTGRES_*; URL-encode @ in AAD users)
python scripts/sync_cfg_profiles_and_action_catalog.py --backend mssql
python scripts/sync_cfg_profiles_and_action_catalog.py --backend postgres
```

Deploy catalog DDL/procs first (once per environment):

```bash
./scripts/deploy_process_pack_catalog.sh --backend mssql --sync
# or
./scripts/deploy_process_pack_catalog.sh --backend postgres --sync
```

Scripts: [`cfg_process_pack_catalog.sql`](../../workflow_engine/sql_mssql/cfg_process_pack_catalog.sql), [`cfg_assay_procedure_links.sql`](../../workflow_engine/sql_mssql/cfg_assay_procedure_links.sql), [`cfg_analyte_catalog.sql`](../../workflow_engine/sql_mssql/cfg_analyte_catalog.sql) (+ PG twins; listed in each dialect’s `deploy_azure.sh`).

The sync upserts `cfg.analyte` from `workflow_engine/domain/analytes/`, then `cfg.pipeline_profile` and `cfg.assay_procedure` from `workflow_engine/domain/profiles/` (and `procedures/`), mapping each document’s `catalog` block to cfg `status` (`published` vs `retired`). Research mode overlays under `profiles/modes/` are **not** synced as profile rows. Assay bind sets `analyte_id` when the analyte row exists; studies with `regulatory.primary_analyte` get `default_analyte_id` backfilled. It also re-seeds `wf.workflow_action` / schemas from `schemas/actions/catalog.json` + `schemas/tasks/` (omit with `--skip-seed`). Portal Study / Start-run pickers use `portal.sp_list_*_catalog` — see [portal-ia](../architecture/portal-ia.md).

## Study scaffold

`methyl-study-init` still writes `/work/projects/<study-id>/` and **also** upserts `cfg.study` when the cfg store is available (`--cfg-store` or `METHYL_CFG_STORE`).

## Credentials

**Production:** EpiPortal admins (`portal.sp_upsert_credential` / `sp_upsert_storage_endpoint` + publish). Never put AccessKeys / SAS URLs in project JSON under `/work/projects`.

**Development / CI:** `methyl-cfg upsert credential` / `storage_endpoint` against `METHYL_CFG_STORE` or DB seed is allowed for bootstrap. Materialize writes **redacted** endpoint metadata only.

Expand at schedule/study-start binds secrets into task `input_json` with `contentHash`. Workers cache node-locally; they never read SQL.

## Study membership

Enroll `portal.Samples` into study arms with `methyl-cfg set-study-group` / `set-study-group-members`. **Starting a study always syncs** cfg → `/work` (`ensure_study_work_synced`); you do not rely on a separate materialize step for correctness. See [How to define samples](../user-manual/How%20to%20define%20samples.md).
