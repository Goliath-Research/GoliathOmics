#!/usr/bin/env bash
# Apply GoliathOmics domain PostgreSQL DDL (child-app canonical path).
#
# Run after GoliathApp platform schema is present on the database.
#
# Example:
#   export PGHOST=localhost PGPORT=5432 PGDATABASE=goliath PGUSER=postgres
#   export PGPASSWORD='...'
#   ./scripts/apply_domain_schema.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SCHEMA_DIR="$REPO_ROOT/database/schema"

# shellcheck source=domain_schema_order.sh
source "$SCRIPT_DIR/domain_schema_order.sh"

: "${PGHOST:?Set PGHOST (libpq host)}"
export PGPORT="${PGPORT:-5432}"
: "${PGDATABASE:?Set PGDATABASE (libpq database name)}"
: "${PGUSER:?Set PGUSER (libpq user)}"
export PGSSLMODE="${PGSSLMODE:-prefer}"

if [[ -z "${PGPASSWORD:-}" && -z "${PGPASSFILE:-}" ]]; then
  echo "Set PGPASSWORD or PGPASSFILE for database authentication." >&2
  exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "psql is not on PATH; install postgresql-client." >&2
  exit 1
fi

for name in "${DOMAIN_SCHEMA_SCRIPTS[@]}"; do
  path="$SCHEMA_DIR/$name"
  if [[ ! -f "$path" ]]; then
    echo "missing domain schema file: $path" >&2
    exit 1
  fi
done

echo "[INFO] GoliathOmics domain schema: database=${PGDATABASE} host=${PGHOST}"
psql -X -q -v ON_ERROR_STOP=1 -c "SELECT current_user AS db_user, version();"

for name in "${DOMAIN_SCHEMA_SCRIPTS[@]}"; do
  echo "[INFO] applying database/schema/${name}"
  psql -X -q -v ON_ERROR_STOP=1 -f "$SCHEMA_DIR/$name"
done

echo "[INFO] domain schema apply complete (${#DOMAIN_SCHEMA_SCRIPTS[@]} files)"
