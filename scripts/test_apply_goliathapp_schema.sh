#!/usr/bin/env bash
# GoliathApp schema once per release: bundle (bundle_goliathapp_sql.py),
# apply (apply_goliathapp_schema.sh) and the deploy_target.sh wiring.
#
# The database cases run against a throwaway PostgreSQL cluster (initdb on a
# unix socket, no TCP). Without server binaries they are skipped, unless
# GOLIATH_REQUIRE_PG_TEST=1 (CI) turns that into a failure.
#
# GOLIATH_APP_SQL_DIR=<GoliathApp>/workflow_engine/sql_pg additionally applies
# the real GoliathApp scripts twice in one database, plus a forced third run,
# to prove the ordered apply is idempotent on an existing database.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="$ROOT/scripts/bundle_goliathapp_sql.py"
OMICS_BUNDLE="$ROOT/scripts/bundle_omics_sql.py"
APPLY="$ROOT/scripts/apply_goliathapp_schema.sh"
OMICS_APPLY="$ROOT/scripts/apply_omics_schema.sh"
DEPLOY="$ROOT/scripts/deploy_target.sh"
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

work="$(mktemp -d /tmp/gas-XXXXXX)"
fixture_profile="$ROOT/deploy/targets/schema-release-ci.json"
pg_data=""
cleanup() {
  if [[ -n "$pg_data" && -f "$pg_data/postmaster.pid" ]]; then
    "$PG_BIN/pg_ctl" -D "$pg_data" -m immediate stop >/dev/null 2>&1 || true
  fi
  rm -f "$fixture_profile"
  rm -rf "$work"
}
trap cleanup EXIT
out="$work/out"
err="$work/err"

# --- fixture GoliathApp sql_pg -------------------------------------------
app="$work/app/sql_pg"
mkdir -p "$app"
cat >"$app/apply_schema.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPTS=(
  00_schema.sql
  # legacy EpiPortal API over tables this database does not have
  10_legacy_api.sql   # trailing comment
  20_api.sql
)
for name in "${SCRIPTS[@]}"; do psql -f "$name"; done
EOF
cat >"$app/00_schema.sql" <<'EOF'
CREATE SCHEMA IF NOT EXISTS wf;
CREATE TABLE IF NOT EXISTS wf.fixture_apply_log (id serial PRIMARY KEY, at timestamptz DEFAULT now());
INSERT INTO wf.fixture_apply_log DEFAULT VALUES;
EOF
cat >"$app/10_legacy_api.sql" <<'EOF'
CREATE SCHEMA IF NOT EXISTS portal;
CREATE OR REPLACE FUNCTION portal.sp_fixture_customers()
RETURNS TABLE (id int) LANGUAGE sql STABLE AS $$
  SELECT "ID" FROM portal."Customers";
$$;
EOF
cat >"$app/20_api.sql" <<'EOF'
CREATE OR REPLACE FUNCTION wf.fixture_answer() RETURNS int LANGUAGE sql AS $$ SELECT 42 $$;
EOF
cat >"$app/99_not_listed.sql" <<'EOF'
SELECT 1/0;
EOF

python3 "$BUNDLE" --sql-dir "$app" --list >"$out"
printf '00_schema.sql\n10_legacy_api.sql\n20_api.sql\n' | diff - "$out" >/dev/null \
  || fail "bundle order must follow apply_schema.sh SCRIPTS: $(tr '\n' ' ' <"$out")"

make_release() {
  local dir="$1" version="$2" sql_dir="$3" commit="${4:-fixturesha1}"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '{"version":"%s"}\n' "$version" >"$dir/manifest.json"
  python3 "$BUNDLE" --sql-dir "$sql_dir" --out "$dir/goliathapp-sql" --commit "$commit" >/dev/null
}

make_omics_release() {
  local dir="$1" version="$2" commit="${3:-omicslocal}"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '{"version":"%s"}\n' "$version" >"$dir/manifest.json"
  python3 "$OMICS_BUNDLE" --sql-dir "$ROOT/workflow_engine/sql_pg" --out "$dir/omics-sql" --commit "$commit" >/dev/null
}

rel1="$work/rel-1"
make_release "$rel1" 1.0.0 "$app"
[[ -f "$rel1/goliathapp-sql/sql_pg/00_schema.sql" ]] || fail "bundle should copy listed scripts"
[[ ! -e "$rel1/goliathapp-sql/sql_pg/99_not_listed.sql" ]] || fail "bundle must copy only listed scripts"
(cd "$rel1/goliathapp-sql" && sha256sum --check --quiet --strict manifest.sha256) || fail "bundle digests do not verify"
jq -e '.commit == "fixturesha1" and .scripts == 3' "$rel1/goliathapp-sql/source.json" >/dev/null \
  || fail "source.json should record the commit and script count"

bad_order="$work/bad-order"
mkdir -p "$bad_order"
printf 'SCRIPTS=(\n  a.sql\n  a.sql\n)\n' >"$bad_order/apply_schema.sh"
if python3 "$BUNDLE" --sql-dir "$bad_order" --list >/dev/null 2>"$err"; then
  fail "a duplicated SCRIPTS entry must be rejected"
fi
printf 'SCRIPTS=(\n  missing.sql\n)\n' >"$bad_order/apply_schema.sh"
if python3 "$BUNDLE" --sql-dir "$bad_order" --out "$work/bad-out" --commit x >/dev/null 2>"$err"; then
  fail "a listed but missing script must fail the bundle"
fi
grep -q 'missing.sql' "$err" || fail "bundle error should name the missing script"

# --- apply without a database ----------------------------------------------
bash "$APPLY" --release-dir "$rel1" --dry-run >"$out"
grep -q 'would apply sql_pg/10_legacy_api.sql' "$out" || fail "dry-run should list the scripts"

old_release="$work/rel-old"
mkdir -p "$old_release"
printf '{"version":"0.9.0"}\n' >"$old_release/manifest.json"
env -u PGHOST bash "$APPLY" --release-dir "$old_release" >"$out" 2>"$err" \
  || fail "a release without goliathapp-sql must not fail the deploy"
grep -q 'no goliathapp-sql' "$err" || fail "missing bundle should be reported"

tampered="$work/rel-tampered"
cp -R "$rel1" "$tampered"
printf -- '-- edited after build\n' >>"$tampered/goliathapp-sql/sql_pg/20_api.sql"
if bash "$APPLY" --release-dir "$tampered" --dry-run >"$out" 2>"$err"; then
  fail "a script that does not match manifest.sha256 must be refused"
fi
grep -q 'does not match manifest.sha256' "$err" || fail "digest refusal should say why"

# The gateway is only the REST gateway: it never receives SQL or the bundle.
gw_files="$(awk '/role" == "gateway"/{p=1} p&&/^    \)/{exit} p' "$DEPLOY")"
if grep -q 'apply_goliathapp_schema\|goliathapp-sql\|sql_pg' <<<"$gw_files"; then
  fail "gateway host file list must not include SQL or the schema apply script"
fi
gw_rel="$work/gw-rel"
cp -R "$rel1" "$gw_rel"
mkdir -p "$gw_rel/wheels"
python3 - "$gw_rel/wheels" <<'PY'
import sys
import zipfile
from pathlib import Path

path = Path(sys.argv[1]) / "corepkg-1.0.0-py3-none-any.whl"
with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("corepkg-1.0.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: corepkg\nVersion: 1.0.0\n\n")
    archive.writestr(
        "corepkg-1.0.0.dist-info/entry_points.txt",
        "[console_scripts]\nmethyl-gateway = rest.gateway:main\n",
    )
PY
python3 "$ROOT/scripts/gateway_release_subset.py" "$gw_rel" "$work/gw-dist" >/dev/null 2>&1 \
  || fail "gateway subset failed on a release with goliathapp-sql"
if find "$work/gw-dist" -name '*.sql' -o -name 'goliathapp-sql' | grep -q .; then
  fail "gateway-dist must not carry GoliathApp SQL"
fi

# --- real PostgreSQL ----------------------------------------------------------
PG_BIN=""
if command -v initdb >/dev/null 2>&1 && command -v pg_ctl >/dev/null 2>&1; then
  PG_BIN="$(dirname "$(command -v initdb)")"
else
  for candidate in $(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V -r); do
    if [[ -x "$candidate/initdb" && -x "$candidate/pg_ctl" ]]; then
      PG_BIN="$candidate"
      break
    fi
  done
fi
if [[ -z "$PG_BIN" || "$(id -u)" -eq 0 ]] || ! command -v psql >/dev/null 2>&1; then
  if [[ "${GOLIATH_REQUIRE_PG_TEST:-0}" == "1" ]]; then
    fail "PostgreSQL server binaries (initdb, pg_ctl), psql and a non-root user are required"
  fi
  echo "SKIP: no PostgreSQL server binaries (or running as root); database cases not run"
  echo "test_apply_goliathapp_schema ok (no database)"
  exit 0
fi

pg_data="$work/pg"
sock="$work/sock"
mkdir -p "$sock"
"$PG_BIN/initdb" -D "$pg_data" -U goliath_admin -A trust >/dev/null
"$PG_BIN/pg_ctl" -D "$pg_data" -w -l "$work/pg.log" \
  -o "-k $sock -c listen_addresses='' -p 54329 -c fsync=off" start >/dev/null \
  || { cat "$work/pg.log" >&2; fail "could not start the scratch PostgreSQL"; }
export PGHOST="$sock" PGPORT=54329 PGUSER=goliath_admin PGDATABASE=postgres
unset PGPASSWORD PGSSLMODE PGCHANNELBINDING PGSERVICE PGOPTIONS
psql_q() { psql -X -q -t -A -v ON_ERROR_STOP=1 "$@"; }
# A non-superuser owner, like neondb_owner on Neon.
psql_q -c "CREATE ROLE app_owner LOGIN" -c "CREATE DATABASE schema_ci OWNER app_owner"
export PGUSER=app_owner PGDATABASE=schema_ci

ledger() { psql_q -c "SELECT release || ':' || apply_count FROM wf.schema_release ORDER BY release"; }
applies() { psql_q -c "SELECT count(*) FROM wf.fixture_apply_log"; }

# First deploy of a release applies and records it.
bash "$APPLY" --release-dir "$rel1" >"$out" 2>"$err" || { cat "$err" >&2; fail "first apply failed"; }
grep -q 'recorded release 1.0.0' "$out" || fail "first apply should record the release"
[[ "$(ledger)" == "1.0.0:1" ]] || fail "ledger after first apply: $(ledger)"
[[ "$(applies)" == "1" ]] || fail "first apply should run the scripts once"
[[ "$(psql_q -c 'SELECT wf.fixture_answer()')" == "42" ]] || fail "scripts did not create functions"
[[ "$(psql_q -c "SELECT goliathapp_sha FROM wf.schema_release WHERE release = '1.0.0'")" == "fixturesha1" ]] \
  || fail "ledger should record the GoliathApp commit"
# Legacy portal function exists even though portal."Customers" does not.
psql_q -c "SELECT 'portal.sp_fixture_customers()'::regprocedure" >/dev/null \
  || fail "legacy API function should be created without its legacy tables"

# Second deploy of the same release skips.
bash "$APPLY" --release-dir "$rel1" >"$out" 2>"$err" || fail "second apply should succeed"
grep -q 'already applied' "$out" || fail "second apply should say the release is already applied"
[[ "$(applies)" == "1" ]] || fail "second deploy of a release must not run the scripts again"
[[ "$(ledger)" == "1.0.0:1" ]] || fail "ledger changed on skip: $(ledger)"

# Same release id, different GoliathApp commit: still skipped, with a warning.
rel1b="$work/rel-1b"
make_release "$rel1b" 1.0.0 "$app" fixturesha2
bash "$APPLY" --release-dir "$rel1b" >"$out" 2>"$err" || fail "re-assembled release apply should succeed"
grep -q 'force-schema' "$err" || fail "a different commit for a recorded release should point at --force-schema"
[[ "$(applies)" == "1" ]] || fail "recorded release must be skipped without --force"

# --force applies again and counts it.
bash "$APPLY" --release-dir "$rel1b" --force >"$out" 2>"$err" || { cat "$err" >&2; fail "forced apply failed"; }
[[ "$(applies)" == "2" ]] || fail "--force should run the scripts again"
[[ "$(ledger)" == "1.0.0:2" ]] || fail "--force should bump apply_count: $(ledger)"
[[ "$(psql_q -c "SELECT goliathapp_sha FROM wf.schema_release WHERE release = '1.0.0'")" == "fixturesha2" ]] \
  || fail "--force should record the new commit"

# A failing script rolls back the whole release and records nothing.
broken="$work/broken/sql_pg"
mkdir -p "$broken"
cp "$app"/*.sql "$broken/"
cat >"$broken/15_new_table.sql" <<'EOF'
CREATE TABLE wf.fixture_should_roll_back (id int);
EOF
cat >"$broken/16_broken.sql" <<'EOF'
SELECT no_such_column FROM wf.fixture_apply_log;
EOF
printf 'SCRIPTS=(\n  00_schema.sql\n  10_legacy_api.sql\n  15_new_table.sql\n  16_broken.sql\n  20_api.sql\n)\n' >"$broken/apply_schema.sh"
rel2="$work/rel-2"
make_release "$rel2" 2.0.0 "$broken"
if bash "$APPLY" --release-dir "$rel2" >"$out" 2>"$err"; then
  fail "a failing script must fail the apply"
fi
grep -q 'not recorded' "$err" || fail "failure should say the release is not recorded"
[[ "$(ledger)" == "1.0.0:2" ]] || fail "failed release must not be recorded: $(ledger)"
[[ "$(applies)" == "2" ]] || fail "failed release must roll back its earlier scripts"
[[ -z "$(psql_q -c "SELECT to_regclass('wf.fixture_should_roll_back')")" ]] \
  || fail "failed release left a table behind"

# --- deploy_target.sh wiring ----------------------------------------------------
cat >"$fixture_profile" <<EOF
{
  "name": "schema-release-ci",
  "rollouts_environment": "goliath-test",
  "database": {
    "kind": "neon",
    "host": "pooler.invalid",
    "schema_host": "$sock",
    "port": 54329,
    "dbname": "schema_ci",
    "user": "app_owner",
    "password": "secret:NEON_TEST_PASSWORD",
    "sslmode": "disable"
  },
  "schema": {"goliathapp": "per-release", "apply": "none"},
  "workers": [
    {"name": "w1", "self_provision": true, "provider": "runpod", "arch": "amd64", "install_root": "/work/goliath"}
  ],
  "runtime": {"root": "/work/goliath"}
}
EOF
run_deploy() {
  env -u CURSOR_API_KEY -u GOLIATH_SCHEMA_FORCE NEON_TEST_PASSWORD=unused-by-trust-auth \
    GOLIATH_ROLLOUTS_SKIP_SERVICES=goliath-db-schema,goliathomics-runtime \
    bash "$DEPLOY" --target schema-release-ci "$@" >"$out" 2>"$err"
}
rel3="$work/rel-3"
make_release "$rel3" 3.0.0 "$app"
GOLIATH_RELEASE_SRC="$rel3" run_deploy --component schema || { cat "$err" >&2; fail "deploy schema failed"; }
grep -q 'recorded release 3.0.0' "$out" || fail "first deploy of 3.0.0 should apply and record it"
[[ "$(applies)" == "3" ]] || fail "deploy should run the release scripts"
GOLIATH_RELEASE_SRC="$rel3" run_deploy --component runtime || { cat "$err" >&2; fail "deploy runtime failed"; }
grep -q 'already applied' "$out" || fail "runtime deploy of a recorded release should skip the schema"
[[ "$(applies)" == "3" ]] || fail "second deploy of 3.0.0 must not apply again"
GOLIATH_RELEASE_SRC="$rel3" run_deploy --component all || { cat "$err" >&2; fail "deploy all failed"; }
[[ "$(grep -c 'already applied' "$out")" == "1" ]] || fail "all should check the schema exactly once"
GOLIATH_RELEASE_SRC="$rel3" run_deploy --component schema --force-schema || fail "forced deploy failed"
[[ "$(applies)" == "4" ]] || fail "--force-schema should apply again"
if grep -q 'unused-by-trust-auth' "$out" "$err"; then
  fail "deploy printed the database password"
fi
rel4="$work/rel-4"
make_release "$rel4" 4.0.0 "$broken"
if GOLIATH_RELEASE_SRC="$rel4" run_deploy --component all; then
  fail "deploy all must stop when the GoliathApp schema fails"
fi
if grep -q 'did not SSH\|skipping SSH promote' "$out"; then
  fail "runtime must not run after a failed schema apply"
fi
[[ "$(ledger)" == "$(printf '1.0.0:2\n3.0.0:2')" ]] || fail "failed deploy must not record 4.0.0: $(ledger)"
# Without a release bundle there is nothing to apply (and no secret needed).
env -u GOLIATH_RELEASE_SRC -u NEON_TEST_PASSWORD -u CURSOR_API_KEY \
  bash "$DEPLOY" --target schema-release-ci --component schema >"$out" 2>"$err" \
  || fail "schema without a release bundle should skip"
grep -q 'GOLIATH_RELEASE_SRC is not set' "$out" || fail "skip should name GOLIATH_RELEASE_SRC"
jq '.schema.goliathapp = "none"' "$fixture_profile" >"$work/p.json" && cp "$work/p.json" "$fixture_profile"
GOLIATH_RELEASE_SRC="$rel4" run_deploy --component schema || fail "goliathapp=none should skip"
grep -q 'schema.goliathapp=none' "$out" || fail "goliathapp=none should be reported"

# --- optional: the real GoliathApp scripts ----------------------------------------
if [[ -n "${GOLIATH_APP_SQL_DIR:-}" ]]; then
  [[ -f "$GOLIATH_APP_SQL_DIR/apply_schema.sh" ]] || fail "GOLIATH_APP_SQL_DIR has no apply_schema.sh"
  export PGUSER=goliath_admin PGDATABASE=postgres
  psql_q -c 'CREATE DATABASE "GoliathOmics" OWNER app_owner'
  export PGUSER=app_owner PGDATABASE=GoliathOmics
  app_sha="$(git -C "$GOLIATH_APP_SQL_DIR" rev-parse HEAD 2>/dev/null || echo local)"
  real1="$work/real-1"
  real2="$work/real-2"
  make_release "$real1" 9.0.0 "$GOLIATH_APP_SQL_DIR" "$app_sha"
  make_release "$real2" 9.0.1 "$GOLIATH_APP_SQL_DIR" "$app_sha"
  bash "$APPLY" --release-dir "$real1" >"$out" 2>"$err" || { tail -5 "$err" >&2; fail "GoliathApp scripts: first apply failed"; }
  bash "$APPLY" --release-dir "$real2" >"$out" 2>"$err" || { tail -5 "$err" >&2; fail "GoliathApp scripts: apply on an existing database failed (not idempotent)"; }
  bash "$APPLY" --release-dir "$real2" --force >"$out" 2>"$err" || { tail -5 "$err" >&2; fail "GoliathApp scripts: forced re-apply failed"; }
  [[ "$(psql_q -c "SELECT string_agg(release || ':' || apply_count, ',' ORDER BY release) FROM wf.schema_release")" == "9.0.0:1,9.0.1:2" ]] \
    || fail "GoliathApp scripts: unexpected ledger"
  echo "GoliathApp sql_pg ($app_sha, $(wc -l <"$real1/goliathapp-sql/manifest.sha256") scripts): applied 3x on one database"

  omics_sha="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo local)"
  omics1="$work/omics-1"
  omics2="$work/omics-2"
  make_omics_release "$omics1" 9.1.0 "$omics_sha"
  make_omics_release "$omics2" 9.1.1 "$omics_sha"
  bash "$OMICS_APPLY" --release-dir "$omics1" >"$out" 2>"$err" \
    || { tail -20 "$err" >&2; fail "Omics schema: first apply failed"; }
  bash "$OMICS_APPLY" --release-dir "$omics2" >"$out" 2>"$err" \
    || { tail -20 "$err" >&2; fail "Omics schema: second apply failed (not idempotent)"; }
  [[ "$(psql_q -c "SELECT to_regclass('portal.\"Customers\"') IS NOT NULL")" == "t" ]] \
    || fail "Omics schema: portal.Customers table missing after apply"
  echo "GoliathOmics sql_pg ($omics_sha): omics-sql applied 2x after GoliathApp on one database"
fi

echo "test_apply_goliathapp_schema ok"
