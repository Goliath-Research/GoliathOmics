#!/usr/bin/env bash
# Deploy process-pack + analyte catalog SQL (cfg.assay_procedure, cfg.analyte,
# portal.sp_* pickers) to Azure SQL and/or PostgreSQL, then optionally sync rows.
#
# Usage:
#   ./scripts/deploy_process_pack_catalog.sh --backend mssql
#   ./scripts/deploy_process_pack_catalog.sh --backend postgres
#   ./scripts/deploy_process_pack_catalog.sh --backend both
#   ./scripts/deploy_process_pack_catalog.sh --backend mssql --sync
#
# Env (mssql): AZURE_SQL_SERVER, AZURE_SQL_DB, AZURE_SQL_USER, AZURE_SQL_PASSWORD
# Env (postgres): PGHOST/PGUSER/PGPASSWORD/PGDATABASE (or POSTGRES_*)
#
# Also applies cfg_repo_api.sql so assay_procedure upsert/publish is available.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$REPO_ROOT/scripts/rollouts_hook.sh"
BACKEND=""
DO_SYNC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      BACKEND="${2:-}"
      shift 2
      ;;
    --sync)
      DO_SYNC=1
      shift
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$BACKEND" ]]; then
  echo "Required: --backend mssql|postgres|both" >&2
  exit 1
fi

deploy_mssql() {
  local dir="$REPO_ROOT/workflow_engine/sql_mssql"
  if ! command -v sqlcmd >/dev/null 2>&1; then
    echo "sqlcmd not found" >&2
    exit 1
  fi
  local server="${AZURE_SQL_SERVER:-}"
  local database="${AZURE_SQL_DB:-MethylPipeline}"
  local user="${AZURE_SQL_USER:-}"
  local password="${AZURE_SQL_PASSWORD:-}"
  if [[ -z "$server" || -z "$user" || -z "$password" ]]; then
    echo "Set AZURE_SQL_SERVER, AZURE_SQL_USER, AZURE_SQL_PASSWORD" >&2
    exit 1
  fi
  local args=(-S "$server" -d "$database" -U "$user" -P "$password" -b -V 16)
  if [[ "${SQLCMD_TRUST_SERVER_CERTIFICATE:-0}" == "1" ]]; then
    args+=(-C)
  fi
  echo "==> mssql: cfg_repo_api.sql (assay_procedure kind)"
  sqlcmd "${args[@]}" -i "$dir/cfg_repo_api.sql"
  echo "==> mssql: cfg_process_pack_catalog.sql"
  sqlcmd "${args[@]}" -i "$dir/cfg_process_pack_catalog.sql"
  echo "==> mssql: cfg_assay_procedure_links.sql"
  sqlcmd "${args[@]}" -i "$dir/cfg_assay_procedure_links.sql"
  echo "==> mssql: cfg_analyte_catalog.sql"
  sqlcmd "${args[@]}" -i "$dir/cfg_analyte_catalog.sql"
  echo "==> mssql: portal_contract_api.sql (catalog @scope_id + pack entitlements)"
  sqlcmd "${args[@]}" -i "$dir/portal_contract_api.sql"
}

deploy_postgres() {
  local dir="$REPO_ROOT/workflow_engine/sql_pg"
  local app_sql="${GOLIATHAPP_SQL_DIR:-}"
  if ! command -v psql >/dev/null 2>&1; then
    echo "psql not found" >&2
    exit 1
  fi
  export PGHOST="${PGHOST:-${POSTGRES_HOST:-}}"
  export PGPORT="${PGPORT:-${POSTGRES_PORT:-5432}}"
  export PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-postgres}}"
  export PGUSER="${PGUSER:-${POSTGRES_USER:-}}"
  export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
  export PGSSLMODE="${PGSSLMODE:-require}"
  if [[ -z "${PGHOST}" || -z "${PGUSER}" || -z "${PGPASSWORD}" ]]; then
    echo "Set PGHOST/PGUSER/PGPASSWORD (or POSTGRES_*)" >&2
    exit 1
  fi
  # GoliathApp-owned DDL (cfg_repo_api, process_pack, portal_contract) is applied
  # by release-deploy via apply_goliathapp_schema.sh. Re-apply from GOLIATHAPP_SQL_DIR
  # or the release bundle when present; otherwise skip with a note.
  if [[ -z "$app_sql" && -n "${GOLIATH_RELEASE_SRC:-}" && -d "${GOLIATH_RELEASE_SRC}/goliathapp-sql/sql_pg" ]]; then
    app_sql="${GOLIATH_RELEASE_SRC}/goliathapp-sql/sql_pg"
  fi
  apply_if_present() {
    local f="$1" label="$2"
    if [[ -f "$f" ]]; then
      echo "==> postgres: $label"
      psql -q -v ON_ERROR_STOP=1 -f "$f"
    else
      echo "==> postgres: skip $label (not found: $f)"
    fi
  }
  if [[ -n "$app_sql" ]]; then
    apply_if_present "$app_sql/cfg_repo_api.sql" "cfg_repo_api.sql (GoliathApp)"
    apply_if_present "$app_sql/cfg_process_pack_catalog.sql" "cfg_process_pack_catalog.sql (GoliathApp)"
    apply_if_present "$app_sql/cfg_assay_procedure_links.sql" "cfg_assay_procedure_links.sql (GoliathApp)"
    apply_if_present "$app_sql/portal_contract_api.sql" "portal_contract_api.sql (GoliathApp)"
  else
    echo "==> postgres: GoliathApp process-pack DDL skipped (set GOLIATHAPP_SQL_DIR or GOLIATH_RELEASE_SRC); expected already applied by release schema"
  fi
  # Omics-owned analyte catalog (not in GoliathApp apply list as of 2026.10.21)
  apply_if_present "$dir/cfg_analyte_catalog.sql" "cfg_analyte_catalog.sql (Omics)"
}

backends=()
case "$BACKEND" in
  mssql) backends=(mssql) ;;
  postgres) backends=(postgres) ;;
  both) backends=(mssql postgres) ;;
  *) echo "Invalid --backend: $BACKEND" >&2; exit 1 ;;
esac

rollouts_begin process-pack-catalog "process pack catalog backend=${BACKEND}"

for b in "${backends[@]}"; do
  if [[ "$b" == "mssql" ]]; then
    deploy_mssql
  else
    deploy_postgres
  fi
  if [[ "$DO_SYNC" == "1" ]]; then
    echo "==> sync analytes/profiles/procedures ($b)"
    (
      cd "$REPO_ROOT"
      # shellcheck disable=SC1091
      source .venv/bin/activate 2>/dev/null || true
      PYTHONPATH=workflow_engine:workers${PYTHONPATH:+:$PYTHONPATH} \
        python scripts/sync_cfg_profiles_and_action_catalog.py \
          --backend "$b" --skip-seed
    )
  fi
done

rollouts_set_message "process pack catalog backend=${BACKEND} sync=${DO_SYNC}"
echo "Process-pack catalog deploy complete (backend=$BACKEND sync=$DO_SYNC)"
