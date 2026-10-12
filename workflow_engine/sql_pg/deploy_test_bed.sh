#!/usr/bin/env bash
# Deploy wf test-bed seeds (PostgreSQL) and optionally simulate a workflow.
#
# Engine DDL (00_schema.sql and the rest of deploy_azure.sh) lives in GoliathApp
# and must already be applied. This script does not embed a database password.
#
#   export PGHOST=... PGDATABASE=goliath PGUSER=... PGPASSWORD=... PGSSLMODE=require
#   ./deploy_test_bed.sh
#   ./deploy_test_bed.sh --run TwoGroupTestFlow
#   ./deploy_test_bed.sh --run McTwoGroupTestFlow

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../scripts/rollouts_hook.sh
source "$REPO_ROOT/scripts/rollouts_hook.sh"

if [[ -z "${PGHOST:-}" ]]; then
  if [[ -n "${POSTGRES_HOST:-}" ]]; then
    export PGHOST="$POSTGRES_HOST"
  else
    echo "PGHOST or POSTGRES_HOST is required (no default host)." >&2
    exit 1
  fi
fi
export PGPORT="${PGPORT:-${POSTGRES_PORT:-5432}}"
export PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-goliath}}"
export PGSSLMODE="${PGSSLMODE:-${POSTGRES_SSLMODE:-require}}"
if [[ -z "${PGUSER:-}" ]]; then
  export PGUSER="${POSTGRES_USER:-}"
fi
if [[ -z "${PGUSER:-}" ]]; then
  echo "PGUSER or POSTGRES_USER is required." >&2
  exit 1
fi
if [[ -z "${PGPASSWORD:-}" && -n "${POSTGRES_PASSWORD:-}" ]]; then
  export PGPASSWORD="$POSTGRES_PASSWORD"
fi

RUN_WORKFLOW=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run)
      RUN_WORKFLOW="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

rollouts_begin goliath-db-schema "sql_pg test bed ${PGHOST}/${PGDATABASE}"

SCRIPTS=(
  wf_instance_extension.sql
  wf_test_bed_schema.sql
  wf_test_bed_worker.sql
  wf_two_group_test_seed.sql
  wf_mc_two_group_test_seed.sql
  wf_test_bed_run.sql
)

for name in "${SCRIPTS[@]}"; do
  echo "Applying $name ..."
  psql -q -v ON_ERROR_STOP=1 -f "$SCRIPT_DIR/$name"
done

echo "Test bed workflows seeded."

if [[ -n "$RUN_WORKFLOW" ]]; then
  CONTEXT_FILE="$SCRIPT_DIR/instance_context_examples/two_group_test.json"
  if [[ "$RUN_WORKFLOW" == "McTwoGroupTestFlow" ]]; then
    CONTEXT_FILE="$SCRIPT_DIR/instance_context_examples/mc_two_group_test.json"
  fi
  echo "Running smoke simulation for $RUN_WORKFLOW ..."
  psql -q -v ON_ERROR_STOP=1 \
    -v workflow_name="$RUN_WORKFLOW" \
    -v context_json="$(cat "$CONTEXT_FILE")" <<'SQL'
SELECT * FROM wf.sp_test_bed_run_workflow(
  :'workflow_name',
  :'context_json'::jsonb
);
SELECT * FROM wf.v_test_bed_task_summary ORDER BY test_bed_run_id DESC LIMIT 1;
SQL
fi

echo "Done."
rollouts_set_message "goliath-db-schema applied on ${PGHOST}/${PGDATABASE}"
