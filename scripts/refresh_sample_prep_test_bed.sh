#!/usr/bin/env bash
# Refresh SamplePrep test bed against Azure SQL (or PostgreSQL via BACKEND_DB).
#
# Requires the same DB env as the REST gateway (see docs/deployment/production_runbook.md).
#
# Usage:
#   source /work/goliath/env/gateway.env   # or export AZURE_SQL_* manually
#   bash scripts/refresh_sample_prep_test_bed.sh
#
# Options:
#   --skip-deploy      Only reseed action catalog
#   --skip-seed        Only deploy workflow definitions

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKIP_DEPLOY=0
SKIP_SEED=0

usage() {
  cat <<'EOF'
Usage: scripts/refresh_sample_prep_test_bed.sh [options]

Refreshes action catalog + SamplePrep workflow definition via direct DB.

Env (Azure SQL):
  AZURE_SQL_SERVER, AZURE_SQL_DB, AZURE_SQL_USER, AZURE_SQL_PASSWORD
  BACKEND_DB=mssql

Env (PostgreSQL):
  BACKEND_DB=postgres
  POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD

Options:
  --skip-deploy      Skip deploy_workflow_definitions.sh
  --skip-seed        Skip seed_action_catalog.py
  -h, --help         Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base|--use-gateway-only)
      echo "Note: gateway admin removed; direct DB only ($1 ignored)" >&2
      if [[ "$1" == "--api-base" ]]; then shift 2; else shift; fi
      ;;
    --skip-deploy) SKIP_DEPLOY=1; shift ;;
    --skip-seed) SKIP_SEED=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN=python3

# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

BACKEND="${BACKEND_DB:-mssql}"
if [[ "$BACKEND" == "mssql" || "$BACKEND" == "sql" ]]; then
  if [[ -z "${AZURE_SQL_SERVER:-}" || -z "${AZURE_SQL_DB:-}" ]]; then
    echo "Azure SQL env required for direct DB seed/deploy" >&2
    exit 1
  fi
  echo "Using Azure SQL backend: ${AZURE_SQL_SERVER}/${AZURE_SQL_DB}"
else
  echo "Using PostgreSQL backend (BACKEND_DB=${BACKEND})"
fi

echo "Exporting task schemas and action catalog..."
methyl-export-task-schemas
methyl-export-action-catalog

if [[ "$SKIP_SEED" -eq 0 ]]; then
  echo "Seeding wf.workflow_action + schemas (direct DB)..."
  "$PYTHON_BIN" "$REPO_ROOT/workflow_engine/sql_mssql/seed_action_catalog.py" --use-db
fi

if [[ "$SKIP_DEPLOY" -eq 0 ]]; then
  echo "Deploying workflow definitions (direct DB) ..."
  bash "$SCRIPT_DIR/deploy_workflow_definitions.sh"
fi

echo "SamplePrep test bed refresh complete."
