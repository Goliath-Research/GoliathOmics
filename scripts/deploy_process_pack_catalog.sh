#!/usr/bin/env bash
# Apply the analyte catalog DDL in this repo and, with --sync, upsert analyte
# documents. Process-pack DDL that lives only in GoliathApp is applied when
# that file is present on disk (--goliath-app).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/deploy_process_pack_catalog.sh [options]

Options:
  --backend postgres|mssql   Default: $BACKEND_DB or postgres
  --sync                     Upsert workflow_engine/domain/analytes/*.analyte.json
  --goliath-app PATH         Also apply <path>/workflow_engine/sql_pg/cfg_process_pack_catalog.sql
                             when that file exists (postgres only)
  --dry-run
  -h, --help

Env (postgres): PGHOST or POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
Env (mssql): AZURE_SQL_SERVER, AZURE_SQL_DB, AZURE_SQL_USER, AZURE_SQL_PASSWORD
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

BACKEND="${BACKEND_DB:-postgres}"
SYNC=0
DRY_RUN=0
GOLIATH_APP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend) BACKEND="${2:-}"; shift 2 ;;
    --sync) SYNC=1; shift ;;
    --goliath-app) GOLIATH_APP="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

case "$BACKEND" in
  postgres|pg) BACKEND=postgres ;;
  mssql|sql) BACKEND=mssql ;;
  *) die "Unknown backend: $BACKEND" ;;
esac

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "dry-run backend=$BACKEND sync=$SYNC"
  exit 0
fi

rollouts_begin process-pack-catalog "process pack catalog backend=${BACKEND}"

export_pg() {
  export PGHOST="${PGHOST:-${POSTGRES_HOST:-}}"
  export PGPORT="${PGPORT:-${POSTGRES_PORT:-5432}}"
  export PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-goliath}}"
  export PGUSER="${PGUSER:-${POSTGRES_USER:-}}"
  export PGSSLMODE="${PGSSLMODE:-${POSTGRES_SSLMODE:-require}}"
  if [[ -z "${PGPASSWORD:-}" && -n "${POSTGRES_PASSWORD:-}" ]]; then
    export PGPASSWORD="$POSTGRES_PASSWORD"
  fi
  [[ -n "$PGHOST" ]] || die "PGHOST or POSTGRES_HOST is required"
  [[ -n "$PGUSER" ]] || die "PGUSER or POSTGRES_USER is required"
}

apply_pg() {
  local file="$1"
  [[ -f "$file" ]] || die "SQL file not found: $file"
  info "Applying $(basename "$file")"
  psql -q -v ON_ERROR_STOP=1 -f "$file"
}

if [[ "$BACKEND" == "postgres" ]]; then
  command -v psql >/dev/null 2>&1 || die "psql is required"
  export_pg
  apply_pg "$REPO_ROOT/workflow_engine/sql_pg/cfg_analyte_catalog.sql"
  if [[ -n "$GOLIATH_APP" && -f "$GOLIATH_APP/workflow_engine/sql_pg/cfg_process_pack_catalog.sql" ]]; then
    apply_pg "$GOLIATH_APP/workflow_engine/sql_pg/cfg_process_pack_catalog.sql"
  fi
  if [[ "$SYNC" -eq 1 ]]; then
    python3 - "$REPO_ROOT/workflow_engine/domain/analytes" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def pg_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


root = Path(sys.argv[1])
for path in sorted(root.glob("*.analyte.json")):
    doc = json.loads(path.read_text(encoding="utf-8"))
    name = str(doc.get("name") or path.stem.split(".")[0])
    version = str(doc.get("version") or "1")
    lifecycle = str((doc.get("catalog") or {}).get("lifecycle") or "active")
    if lifecycle == "active":
        status = "published"
    elif lifecycle == "retired":
        status = "retired"
    else:
        status = "draft"
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    stmt = f"""
INSERT INTO cfg.analyte (name, version, status, content_hash, document_json)
VALUES (
  {pg_literal(name)},
  {pg_literal(version)},
  {pg_literal(status)},
  {pg_literal(digest)},
  {pg_literal(payload)}::jsonb
)
ON CONFLICT (name, version) DO UPDATE SET
  status = EXCLUDED.status,
  content_hash = EXCLUDED.content_hash,
  document_json = EXCLUDED.document_json,
  updated_at_utc = (now() AT TIME ZONE 'utc');
"""
    subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1"],
        input=stmt,
        text=True,
        check=True,
    )
    print(f"synced analyte {name}@{version} status={status}")
PY
  fi
else
  command -v sqlcmd >/dev/null 2>&1 || die "sqlcmd is required for --backend mssql"
  server="${AZURE_SQL_SERVER:-}"
  database="${AZURE_SQL_DB:-}"
  user="${AZURE_SQL_USER:-}"
  [[ -n "$server" && -n "$database" && -n "$user" ]] || die "AZURE_SQL_SERVER, AZURE_SQL_DB, and AZURE_SQL_USER are required"
  info "Applying cfg_analyte_catalog.sql (mssql)"
  sqlcmd -S "$server" -d "$database" -U "$user" -b -i "$REPO_ROOT/workflow_engine/sql_mssql/cfg_analyte_catalog.sql"
  if [[ "$SYNC" -eq 1 ]]; then
    die "--sync for mssql requires the GoliathApp catalog sync; postgres --sync is implemented here"
  fi
fi

info "process pack catalog step finished"
