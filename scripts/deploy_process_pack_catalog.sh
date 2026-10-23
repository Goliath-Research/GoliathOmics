#!/usr/bin/env bash
# Deploy process-pack + analyte catalog SQL (cfg.assay_procedure, cfg.analyte,
# portal.sp_* pickers) to PostgreSQL, then optionally sync rows.
#
# Usage:
#   ./scripts/deploy_process_pack_catalog.sh
#   ./scripts/deploy_process_pack_catalog.sh --sync
#
# Env: PGHOST/PGUSER/PGPASSWORD/PGDATABASE (or POSTGRES_*)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$REPO_ROOT/scripts/rollouts_hook.sh"
DO_SYNC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      if [[ "${2:-}" != "postgres" ]]; then
        echo "Only --backend postgres is supported (SQL Server was removed)." >&2
        exit 1
      fi
      shift 2
      ;;
    --sync)
      DO_SYNC=1
      shift
      ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

deploy_postgres() {
  local dir="$REPO_ROOT/workflow_engine/sql_pg"
  local app_sql="${GOLIATHAPP_SQL_DIR:-${GOLIATH_APP_SQL_DIR:-}}"
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
    apply_if_present "$app_sql/cfg_schema.sql" "cfg_schema.sql (GoliathApp)"
    apply_if_present "$app_sql/cfg_repo_api.sql" "cfg_repo_api.sql (GoliathApp)"
    apply_if_present "$app_sql/cfg_process_pack_catalog.sql" "cfg_process_pack_catalog.sql (GoliathApp)"
    apply_if_present "$app_sql/cfg_assay_procedure_links.sql" "cfg_assay_procedure_links.sql (GoliathApp)"
    apply_if_present "$app_sql/portal_contract_api.sql" "portal_contract_api.sql (GoliathApp)"
  else
    echo "==> postgres: GoliathApp process-pack DDL skipped (set GOLIATHAPP_SQL_DIR or GOLIATH_RELEASE_SRC); expected already applied by release schema"
  fi
  # Omics-owned analyte catalog (not in GoliathApp apply list as of 2026.10.21)
  psql -q -v ON_ERROR_STOP=1 -c 'CREATE SCHEMA IF NOT EXISTS cfg;'
  apply_if_present "$dir/cfg_analyte_catalog.sql" "cfg_analyte_catalog.sql (Omics)"
}

rollouts_begin process-pack-catalog "process pack catalog postgres"

deploy_postgres
if [[ "$DO_SYNC" == "1" ]]; then
  echo "==> sync analytes/profiles/procedures (postgres)"
  (
    cd "$REPO_ROOT"
    # shellcheck disable=SC1091
    source .venv/bin/activate 2>/dev/null || true
    PYTHONPATH=workflow_engine:workers${PYTHONPATH:+:$PYTHONPATH} \
      python scripts/sync_cfg_profiles_and_action_catalog.py \
        --backend postgres --skip-seed
  )
fi

rollouts_set_message "process pack catalog postgres sync=${DO_SYNC}"
echo "Process-pack catalog deploy complete (sync=$DO_SYNC)"
