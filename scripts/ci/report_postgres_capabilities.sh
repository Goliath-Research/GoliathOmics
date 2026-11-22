#!/usr/bin/env bash
# Fail unless the CI PostgreSQL service is the expected major, and log versions.
# Connection comes from the standard libpq variables (PGHOST, PGUSER, ...).
set -euo pipefail

expected_major="${EXPECTED_PG_MAJOR:?EXPECTED_PG_MAJOR is required}"

num="$(psql -v ON_ERROR_STOP=1 -X -At -c "SHOW server_version_num")"
actual_major="$((num / 10000))"
echo "server_version: $(psql -X -At -c "SHOW server_version")"
echo "psql: $(psql --version)"

if [[ "${actual_major}" != "${expected_major}" ]]; then
  echo "Expected PostgreSQL ${expected_major}, got ${actual_major}" >&2
  exit 1
fi
