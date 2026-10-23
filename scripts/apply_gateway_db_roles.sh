#!/usr/bin/env bash
# Set login passwords on goliath_portal / goliath_gateway after GoliathApp schema.
# Roles and grants are created by database_gateway_roles.sql (GoliathApp bundle).
# Passwords come from the environment only (deploy_target resolves secret: refs).
set -euo pipefail

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

PORTAL_PASSWORD="${GOLIATH_PORTAL_DB_PASSWORD:-}"
GATEWAY_PASSWORD="${GOLIATH_GATEWAY_DB_PASSWORD:-}"
PORTAL_USER="${GOLIATH_PORTAL_DB_USER:-goliath_portal}"
GATEWAY_USER="${GOLIATH_GATEWAY_DB_USER:-goliath_gateway}"

if [[ -z "$PORTAL_PASSWORD" && -z "$GATEWAY_PASSWORD" ]]; then
  info "no GOLIATH_PORTAL_DB_PASSWORD or GOLIATH_GATEWAY_DB_PASSWORD; skipping role passwords"
  exit 0
fi

for var in PGHOST PGDATABASE PGUSER PGPASSWORD; do
  if [[ -z "${!var:-}" ]]; then
    die "apply_gateway_db_roles requires ${var} (export_schema_pg_env from deploy_target)"
  fi
done

psql_q() {
  psql -qAt -v ON_ERROR_STOP=1 "$@"
}

_sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

if [[ -n "$PORTAL_PASSWORD" ]]; then
  local_esc="$(_sql_escape "$PORTAL_PASSWORD")"
  info "setting password for role ${PORTAL_USER}"
  psql_q -c "ALTER ROLE \"${PORTAL_USER}\" WITH LOGIN PASSWORD '${local_esc}';"
fi

if [[ -n "$GATEWAY_PASSWORD" ]]; then
  local_esc="$(_sql_escape "$GATEWAY_PASSWORD")"
  info "setting password for role ${GATEWAY_USER}"
  psql_q -c "ALTER ROLE \"${GATEWAY_USER}\" WITH LOGIN PASSWORD '${local_esc}';"
fi

info "gateway DB role passwords applied"
