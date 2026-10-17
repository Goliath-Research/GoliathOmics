#!/usr/bin/env bash
# Apply the GoliathApp PostgreSQL scripts bundled in a release, once per
# release.
#
# The release carries goliathapp-sql/ (scripts/bundle_goliathapp_sql.py at
# build time): GoliathApp workflow_engine/sql_pg in deploy_azure.sh order,
# with sha256 digests and the GoliathApp commit. These scripts are the
# database definition. Nothing else patches the database.
#
# wf.schema_release records each release whose scripts were applied. A
# release already recorded there is skipped, so the first deploy of a release
# upgrades the database and later deploys (pod restarts, re-runs, other
# components) do not touch it. --force applies again and bumps apply_count.
#
# Everything runs in one transaction under an advisory lock: the scripts, the
# ledger table, and the ledger row. A failing script rolls all of it back and
# the release stays unrecorded, so the next deploy tries again.
#
# check_function_bodies is off inside that transaction, as in pg_dump output.
# Some LANGUAGE sql functions read legacy EpiPortal tables (portal."Customers",
# portal."Role2Node", portal."Diseases") that only exist on databases migrated
# from EpiPortal. The functions are still created; on a database without
# those tables they fail when called, not when the release is deployed.
#
# Connection: libpq PG* variables (PGHOST, PGPORT, PGDATABASE, PGUSER,
# PGPASSWORD, PGSSLMODE, PGCHANNELBINDING). The password is never printed.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/apply_goliathapp_schema.sh --release-dir DIR [options]

Options:
  --release-dir DIR   Release tree with manifest.json and goliathapp-sql/
  --release ID        Release id to record (default: manifest.json version)
  --force             Apply even if wf.schema_release already has the release
  --dry-run           Verify the bundle and print the plan; no database access
  -h, --help

Environment:
  GOLIATH_SCHEMA_LOCK_TIMEOUT  lock_timeout for the apply transaction (default 120s)
EOF
}

RELEASE_DIR=""
RELEASE_ID=""
FORCE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR="${2:-}"; shift 2 ;;
    --release) RELEASE_ID="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$RELEASE_DIR" ]] || die "--release-dir is required"
[[ -d "$RELEASE_DIR" ]] || die "release dir not found: $RELEASE_DIR"
RELEASE_DIR="$(cd "$RELEASE_DIR" && pwd)"
manifest="$RELEASE_DIR/manifest.json"
[[ -f "$manifest" ]] || die "release has no manifest.json: $RELEASE_DIR"
command -v jq >/dev/null 2>&1 || die "jq is required"

if [[ -z "$RELEASE_ID" ]]; then
  RELEASE_ID="$(jq -r '.version // empty' "$manifest")"
fi
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || die "release id is missing or unsafe: '${RELEASE_ID}'"

sql_root="$RELEASE_DIR/goliathapp-sql"
order_file="$sql_root/manifest.sha256"
if [[ ! -f "$order_file" ]]; then
  # Releases built before the bundle existed. Nothing to apply.
  warn "release ${RELEASE_ID} has no goliathapp-sql/manifest.sha256; GoliathApp schema not applied"
  exit 0
fi
[[ -f "$sql_root/source.json" ]] || die "goliathapp-sql/source.json is missing in release ${RELEASE_ID}"
app_sha="$(jq -r '.commit // empty' "$sql_root/source.json")"
[[ "$app_sha" =~ ^[A-Za-z0-9._-]+$ ]] || die "goliathapp-sql/source.json has no usable commit"
[[ "$sql_root" != *\'* ]] || die "release path contains a quote: $sql_root"

# Every listed file must match its digest before anything reaches the database.
if ! (cd "$sql_root" && sha256sum --check --quiet --strict manifest.sha256); then
  die "goliathapp-sql does not match manifest.sha256 in release ${RELEASE_ID}"
fi
manifest_sha="$(sha256sum "$order_file" | awk '{print $1}')"

scripts=()
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -n "$line" ]] || continue
  name="${line#*  }"
  [[ "$name" =~ ^sql_pg/[A-Za-z0-9_][A-Za-z0-9_.-]*\.sql$ ]] || die "unexpected entry in manifest.sha256: $name"
  scripts+=("$name")
done <"$order_file"
[[ "${#scripts[@]}" -gt 0 ]] || die "goliathapp-sql/manifest.sha256 is empty"

lock_timeout="${GOLIATH_SCHEMA_LOCK_TIMEOUT:-120s}"
[[ "$lock_timeout" =~ ^[0-9]+(ms|s|min)?$ ]] || die "GOLIATH_SCHEMA_LOCK_TIMEOUT must look like 120s, 2min or 5000ms"

info "GoliathApp schema: release=${RELEASE_ID} goliathapp=${app_sha} scripts=${#scripts[@]} force=${FORCE}"
if [[ "$DRY_RUN" -eq 1 ]]; then
  for name in "${scripts[@]}"; do
    info "  would apply ${name}"
  done
  info "dry run: database not contacted"
  exit 0
fi

command -v psql >/dev/null 2>&1 || die "psql is required"
psql_base=(psql -X -q -v ON_ERROR_STOP=1)

# Fast path outside the transaction: the ledger already has this release.
recorded="$("${psql_base[@]}" -t -A -v release="$RELEASE_ID" <<'SQL'
SELECT to_regclass('wf.schema_release') IS NOT NULL AS has_ledger \gset
\if :has_ledger
SELECT goliathapp_sha || ' ' || to_char(applied_at_utc, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
FROM wf.schema_release WHERE release = :'release';
\endif
SQL
)" || die "could not read wf.schema_release (connection or permission problem)"

if [[ -n "$recorded" && "$FORCE" -ne 1 ]]; then
  recorded_sha="${recorded%% *}"
  info "release ${RELEASE_ID} is already applied (goliathapp ${recorded_sha} at ${recorded#* }); skipping"
  if [[ "$recorded_sha" != "$app_sha" ]]; then
    warn "release ${RELEASE_ID} was applied from goliathapp ${recorded_sha} but this bundle is ${app_sha}; rerun with --force-schema to apply it"
  fi
  exit 0
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
plan="$work/apply.sql"
force_flag=off
if [[ "$FORCE" -eq 1 ]]; then
  force_flag=on
fi
{
  cat <<'SQL'
SET client_min_messages = warning;
BEGIN;
SET LOCAL lock_timeout = :'lock_timeout';
-- Functions over legacy EpiPortal tables are created even where those tables
-- do not exist (see the header of apply_goliathapp_schema.sh).
SET LOCAL check_function_bodies = off;
-- One schema apply at a time per database ("goliath schema_release").
SELECT pg_advisory_xact_lock(7243512096) \gset
CREATE SCHEMA IF NOT EXISTS wf;
CREATE TABLE IF NOT EXISTS wf.schema_release (
  release text PRIMARY KEY,
  goliathapp_sha text NOT NULL,
  manifest_sha256 text NOT NULL,
  script_count integer NOT NULL,
  applied_at_utc timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  applied_by text NOT NULL DEFAULT current_user,
  apply_count integer NOT NULL DEFAULT 1
);
COMMENT ON TABLE wf.schema_release IS
  'GoliathOmics releases whose GoliathApp sql_pg scripts were applied (scripts/apply_goliathapp_schema.sh).';
SELECT EXISTS (SELECT 1 FROM wf.schema_release WHERE release = :'release') AS already_applied \gset
\if :already_applied
\if :force
\echo [INFO] release :release is recorded; --force applies it again
\else
\echo [INFO] release :release was recorded by a concurrent deploy; skipping
ROLLBACK;
\quit
\endif
\endif
SQL
  for name in "${scripts[@]}"; do
    printf '\\echo [INFO] applying %s\n' "$name"
    printf "\\\\i '%s/%s'\n" "$sql_root" "$name"
  done
  cat <<'SQL'
INSERT INTO wf.schema_release (release, goliathapp_sha, manifest_sha256, script_count)
VALUES (:'release', :'goliathapp_sha', :'manifest_sha256', :script_count)
ON CONFLICT (release) DO UPDATE SET
  goliathapp_sha = EXCLUDED.goliathapp_sha,
  manifest_sha256 = EXCLUDED.manifest_sha256,
  script_count = EXCLUDED.script_count,
  applied_at_utc = EXCLUDED.applied_at_utc,
  applied_by = EXCLUDED.applied_by,
  apply_count = wf.schema_release.apply_count + 1;
COMMIT;
\echo [INFO] recorded release :release in wf.schema_release
SQL
} >"$plan"

if ! "${psql_base[@]}" \
  -v release="$RELEASE_ID" \
  -v goliathapp_sha="$app_sha" \
  -v manifest_sha256="$manifest_sha" \
  -v script_count="${#scripts[@]}" \
  -v force="$force_flag" \
  -v lock_timeout="$lock_timeout" \
  -f "$plan"; then
  die "GoliathApp schema apply failed for release ${RELEASE_ID}; the transaction was rolled back and the release is not recorded"
fi
info "GoliathApp schema is at release ${RELEASE_ID} (goliathapp ${app_sha})"
