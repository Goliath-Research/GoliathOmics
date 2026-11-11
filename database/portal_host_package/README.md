# GoliathOmics portal host package

GoliathOmics extends **GoliathApp** on a **dedicated PostgreSQL database** served by the shared platform host (hostname → product/database). GoliathApp stays product-neutral; this directory owns Omics Meta subclasses, NavTree, and apply scripts.

## Target database name (parameter)

| Environment | Database name | Notes |
|-------------|---------------|--------|
| Production (planned) | `GoliathOmics_v2` | **Do not create until David approves.** |
| Legacy (frozen) | `GoliathOmics` | Do not rename or apply here until David schedules `GoliathOmics_legacy` cutover. |
| CI / local | `goliathomics_portal_host_test` (ephemeral) | Created by pytest only. |

Set libpq variables (`PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`) or pass `TARGET_PGDATABASE` to `apply_package.sh`. Scripts do **not** embed connection secrets.

## GoliathApp baseline pin

CI and operators apply the generic App stack from a pinned ref: `goliathapp_baseline.ref` (currently `017731d5d7e3f3580a57e1c54cdb6b60509f853e`). Bump the ref file when adopting a newer GoliathApp portal baseline.

## Apply order (new database)

On an empty database (or after Neon branch create):

1. GoliathApp: `bash workflow_engine/sql_pg/apply_schema.sh`
2. GoliathApp catalog seeds: `seed_semantic_catalog.py`, `seed_action_catalog.py --use-db`
3. GoliathApp: `psql -f workflow_engine/sql_pg/seed_platform_nav_tree.sql`
4. **This package:** `bash database/portal_host_package/apply_package.sh`

Re-running steps 1–4 is idempotent.

## Platform host (GoliathApp PR)

Hostname routing, `GOLIATH_PLATFORM_PRODUCTS_JSON`, and per-product `goliath_portal` credentials are configured on the **GoliathApp** platform host. Production steps for VM **45.63.16.235** (Caddy, Entra redirect `https://omics.goliathresearch.com/...`, env vars) require David’s approval — see GoliathApp `docs/deploy/multi-product-platform-host.md` after the multi-product host merges.

## Security

Same Meta security model as GoliathApp (`platform/User`, grants cache, single active role). **`god_architect`** receives the operator superset via `portal.sp_sync_god_architect_operator_entitlements()` when present (called from `02_omics_nav_seed.sql`).
