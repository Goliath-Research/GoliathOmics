#!/usr/bin/env bash
# Apply GoliathOmics PostgreSQL domain schema (ordered SCRIPTS list).
# Run after GoliathApp workflow_engine/sql_pg/apply_schema.sh on the same database.
#
# Example:
#   export PGHOST=localhost PGPORT=5432 PGDATABASE=goliath PGUSER=postgres PGPASSWORD='...'
#   bash /path/to/GoliathApp/workflow_engine/sql_pg/apply_schema.sh
#   bash ./apply_schema.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${PGHOST:?Set PGHOST (libpq host)}"
export PGPORT="${PGPORT:-5432}"
: "${PGDATABASE:?Set PGDATABASE (libpq database name)}"
: "${PGUSER:?Set PGUSER (libpq user)}"
export PGSSLMODE="${PGSSLMODE:-prefer}"

if [[ -z "${PGPASSWORD:-}" && -z "${PGPASSFILE:-}" ]]; then
  echo "Set PGPASSWORD or PGPASSFILE for database authentication." >&2
  exit 1
fi

SCRIPTS=(
  portal_clinical_schema.sql
  portal_sample_extras_schema.sql
  legacy_cross_schema_fks.sql
  portal_clinical_api.sql
  portal_clinical_api_parity.sql
  portal_clinical_nav.sql
  cfg_analyte_catalog.sql
)

echo "Omics schema target: host=$PGHOST db=$PGDATABASE user=$PGUSER sslmode=$PGSSLMODE"

for name in "${SCRIPTS[@]}"; do
  path="$SCRIPT_DIR/$name"
  if [[ ! -f "$path" ]]; then
    echo "Missing script: $path" >&2
    exit 1
  fi
  echo "Applying $name ..."
  psql -q -v ON_ERROR_STOP=1 -f "$path"
done

echo "GoliathOmics portal domain schema applied on $PGHOST/$PGDATABASE"
