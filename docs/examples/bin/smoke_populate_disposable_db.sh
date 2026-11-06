#!/usr/bin/env bash
# Disposable Postgres: apply GoliathApp + Omics bundles → cfg JSON sync → ActionSpec/runtime seed →
# deploy DomainPrograms → minimal cfg.study + study_start_request (SQL enqueue).
#
# Does NOT start the gateway, workers, or run SamplePrep/tasks.
#
# Requires:
#   - initdb, pg_ctl, psql (apt install postgresql)
#   - jq, python3, python3-venv
#   - GOLIATH_APP_SQL_DIR → .../GoliathApp/workflow_engine/sql_pg
#   - GOLIATH_APP_ROOT → .../GoliathApp (parent of workflow_engine; for pip install -e workflow_engine)
#
# Usage:
#   export GOLIATH_APP_SQL_DIR=/path/GoliathApp/workflow_engine/sql_pg
#   export GOLIATH_APP_ROOT=/path/GoliathApp
#   export OMICS_REPO=/path/GoliathOmics   # default: inferred from script location
#   bash docs/examples/bin/smoke_populate_disposable_db.sh
#
set -euo pipefail
set -o pipefail

OMICS_REPO="${OMICS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
GOLIATH_APP_ROOT="${GOLIATH_APP_ROOT:-}"
if [[ -z "$GOLIATH_APP_ROOT" && -n "${GOLIATH_APP_SQL_DIR:-}" ]]; then
  GOLIATH_APP_ROOT="$(cd "$GOLIATH_APP_SQL_DIR/../.." && pwd)"
fi

[[ -d "$OMICS_REPO" ]] || { echo "OMICS_REPO not found: $OMICS_REPO" >&2; exit 1; }
[[ -n "${GOLIATH_APP_SQL_DIR:-}" && -f "$GOLIATH_APP_SQL_DIR/apply_schema.sh" ]] || {
  echo "Set GOLIATH_APP_SQL_DIR to GoliathApp workflow_engine/sql_pg" >&2
  exit 1
}
[[ -f "$GOLIATH_APP_ROOT/workflow_engine/pyproject.toml" ]] || {
  echo "Set GOLIATH_APP_ROOT to GoliathApp repo root (with workflow_engine/pyproject.toml)" >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || { echo "jq is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

PG_BIN=""
if command -v initdb >/dev/null 2>&1 && command -v pg_ctl >/dev/null 2>&1; then
  PG_BIN="$(dirname "$(command -v initdb)")"
elif [[ -x "/usr/lib/postgresql/16/bin/initdb" ]]; then
  PG_BIN="/usr/lib/postgresql/16/bin"
fi
[[ -n "$PG_BIN" ]] || { echo "initdb/pg_ctl not found; install postgresql" >&2; exit 1; }

_run_smoke_pass() {
  local pass="$1"
  local work="$2"

  export PGDATA="$work/pgdata"
  export PGSOCKET="$work/sock"
  mkdir -p "$PGSOCKET"
  # TCP on localhost: `rest.connection.build_postgres_conninfo` builds a postgresql:// URI; Unix socket
  # paths in PGHOST break URI parsing. Pick a free ephemeral port per pass.
  local PG_PORT
  PG_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1]); s.close()')"
  "$PG_BIN/initdb" -D "$PGDATA" -U goliath_admin -A trust >/dev/null
  "$PG_BIN/pg_ctl" -D "$PGDATA" -w -l "$work/pg.log" \
    -o "-k $PGSOCKET -p $PG_PORT -c listen_addresses=127.0.0.1 -c fsync=off" start >/dev/null \
    || { cat "$work/pg.log" >&2; echo "postgres start failed (pass $pass)" >&2; return 1; }

  _smoke_stop_pg() {
    [[ -n "${PGDATA:-}" && -n "${PG_BIN:-}" ]] || return 0
    [[ -d "$PGDATA" ]] || return 0
    "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null 2>&1 || true
  }
  trap '_smoke_stop_pg' EXIT

  export PGHOST=127.0.0.1
  export PGPORT="$PG_PORT"
  export PGUSER=goliath_admin
  export PGDATABASE=postgres
  export PGSSLMODE=disable
  unset PGPASSWORD

  psql_q() { psql -X -v ON_ERROR_STOP=1 "$@"; }

  psql_q -c "CREATE DATABASE pop_smoke OWNER goliath_admin"
  export PGDATABASE=pop_smoke

  REL="$work/release"
  mkdir -p "$REL"
  printf '{"version":"doc-smoke"}\n' >"$REL/manifest.json"
  APP_COMMIT="$(git -C "$GOLIATH_APP_SQL_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  OMICS_COMMIT="$(git -C "$OMICS_REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
  python3 "$OMICS_REPO/scripts/bundle_goliathapp_sql.py" \
    --sql-dir "$GOLIATH_APP_SQL_DIR" \
    --out "$REL/goliathapp-sql" \
    --commit "$APP_COMMIT" >/dev/null
  python3 "$OMICS_REPO/scripts/bundle_omics_sql.py" \
    --sql-dir "$OMICS_REPO/workflow_engine/sql_pg" \
    --out "$REL/omics-sql" \
    --commit "$OMICS_COMMIT" >/dev/null

  bash "$OMICS_REPO/scripts/apply_goliathapp_schema.sh" --release-dir "$REL" >/dev/null
  bash "$OMICS_REPO/scripts/apply_omics_schema.sh" --release-dir "$REL" >/dev/null

  # Second apply: must skip (ledger)
  bash "$OMICS_REPO/scripts/apply_goliathapp_schema.sh" --release-dir "$REL" >"$work/second_ga.log" 2>&1 || true
  grep -E 'already applied|skipping' "$work/second_ga.log" >/dev/null || {
    echo "pass $pass: expected second GoliathApp apply to skip; log:" >&2
    cat "$work/second_ga.log" >&2
    return 1
  }

  VENV="$work/venv"
  if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -U pip
    "$VENV/bin/pip" install -q "psycopg[binary]>=3.1" pydantic psutil
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/packages/methylutils"
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/packages/methyldomain"
    # Domain step-override models import these packages (Poetry projects; pip -e is enough for imports).
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/packages/methyldetector"
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/packages/methylmapper"
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/packages/methylenricher"
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/packages/methyldiseaseprogression"
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/packages/methylclassifier"
    "$VENV/bin/pip" install -q -e "$GOLIATH_APP_ROOT/workflow_engine"
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/domain" --no-deps
    "$VENV/bin/pip" install -q -e "$OMICS_REPO/docs/examples/python"
  fi

  # GoliathApp: repo root (goliath_app) + workflow_engine (domain.compiler) must precede Omics workers on PYTHONPATH.
  export PYTHONPATH="$GOLIATH_APP_ROOT:$GOLIATH_APP_ROOT/workflow_engine:$OMICS_REPO/workers:$OMICS_REPO/domain/src${PYTHONPATH:+:$PYTHONPATH}"

  "$VENV/bin/python" "$OMICS_REPO/scripts/sync_cfg_profiles_and_action_catalog.py" \
    --backend postgres --skip-seed || return 1

  "$VENV/bin/python" "$OMICS_REPO/workflow_engine/sql_pg/seed_action_catalog.py" --use-db || return 1

  export BACKEND_DB=postgres
  export POSTGRES_HOST="$PGHOST"
  export POSTGRES_PORT="${PGPORT:-5432}"
  export POSTGRES_DB="$PGDATABASE"
  export POSTGRES_USER="$PGUSER"
  unset POSTGRES_PASSWORD
  export GOLIATH_ENV_DIR="$work/goliath-env"
  # deploy_workflow_definitions.sh defaults to python3; use this venv (pydantic, deps for domain.compiler).
  export PYTHON_BIN="$VENV/bin/python"
  bash "$OMICS_REPO/scripts/deploy_workflow_definitions.sh" || return 1

  psql_q -f "$OMICS_REPO/docs/examples/sql/minimal_study_for_sample_prep_enqueue.sql" || return 1

  STUDY_ID="$(psql_q -t -A -c "SELECT id FROM cfg.study WHERE name = 'docsdemo_study' AND version = '1'")"
  [[ -n "$STUDY_ID" ]] || { echo "pass $pass: study id missing" >&2; return 1; }

  # psql -v name=value does not expand ${var} inside double quotes — use concatenation so the id is numeric.
  psql_q \
    -v "study_row_id=$STUDY_ID" \
    -v "workflow_version_id=0" \
    -v "project_path=/work/projects/docsdemo_study/configs/project_docsdemo_study.json" \
    -v "samples_json=[{\"sampleId\":\"smoke-1\",\"fastqPrefix\":\"smoke-1/\"}]" \
    -v "fastq_storage_json={\"type\":\"file\",\"basePath\":\"/tmp/fastqs\"}" \
    -f "$OMICS_REPO/workflow_engine/sql_pg/enqueue_sample_prep_test_start.sql" || return 1

  WF_COUNT="$(psql_q -t -A -c "SELECT count(*) FROM wf.workflow_version v JOIN wf.workflow_def d ON d.id = v.workflow_def_id WHERE d.name = 'SamplePrepPipeline' AND v.is_active AND v.root_node_id IS NOT NULL")"
  [[ "$WF_COUNT" -ge 1 ]] || { echo "pass $pass: no active SamplePrepPipeline version" >&2; return 1; }
  Q_COUNT="$(psql_q -t -A -c "SELECT count(*) FROM cfg.study_start_request WHERE status = 'queued'")"
  [[ "$Q_COUNT" -ge 1 ]] || { echo "pass $pass: no queued cfg.study_start_request" >&2; return 1; }

  psql_q -f "$OMICS_REPO/docs/examples/sql/verify_schema_ledgers.sql" || return 1
  psql_q -f "$OMICS_REPO/docs/examples/sql/verify_population_invariants.sql" || return 1

  trap - EXIT
  _smoke_stop_pg
  echo "OK smoke pass $pass"
}

for PASS in 1 2; do
  WORK="$(mktemp -d /tmp/golio-pop-XXXXXX)"
  _run_smoke_pass "$PASS" "$WORK" || exit 1
  rm -rf "$WORK"
done

echo "OK: smoke_populate_disposable_db (two full passes; no gateway/workers)"
