#!/usr/bin/env bash
# Fail unless the initdb/pg_ctl/psql on PATH are the expected PostgreSQL major.
# Used by jobs that start a throwaway local cluster instead of a service container.
set -euo pipefail

expected_major="${EXPECTED_PG_MAJOR:?EXPECTED_PG_MAJOR is required}"

for tool in initdb pg_ctl psql; do
  version_line="$("${tool}" --version)"
  echo "${tool}: $(command -v "${tool}") -> ${version_line}"
  actual_major="$(sed -E 's/^[^0-9]*([0-9]+).*/\1/' <<<"${version_line}")"
  if [[ "${actual_major}" != "${expected_major}" ]]; then
    echo "Expected PostgreSQL ${expected_major} for ${tool}, got ${actual_major}" >&2
    exit 1
  fi
done
