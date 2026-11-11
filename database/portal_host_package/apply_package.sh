#!/usr/bin/env bash
# Apply GoliathOmics portal host SQL package on the database in PG* / TARGET_PGDATABASE.
set -euo pipefail

if [[ -n "${TARGET_PGDATABASE:-}" ]]; then
  export PGDATABASE="$TARGET_PGDATABASE"
fi

: "${PGHOST:?Set PGHOST}"
: "${PGDATABASE:?Set PGDATABASE or TARGET_PGDATABASE (e.g. GoliathOmics_v2 when approved)}"
: "${PGUSER:?Set PGUSER}"
export PGPORT="${PGPORT:-5432}"
export PGSSLMODE="${PGSSLMODE:-prefer}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_DIR="${SCRIPT_DIR}/sql"

for sql in 01_omics_meta_domain_classes.sql 02_omics_nav_seed.sql; do
  echo "Applying ${sql}..."
  psql -v ON_ERROR_STOP=1 -f "${SQL_DIR}/${sql}"
done

echo "GoliathOmics portal host package applied on ${PGDATABASE}@${PGHOST}."
