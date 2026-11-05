#!/usr/bin/env bash
# Apply GoliathOmics PostgreSQL domain schema (legacy entry; delegates to child-app apply).
# Run after GoliathApp workflow_engine/sql_pg/apply_schema.sh on the same database.
#
# Canonical DDL lives in database/schema/ (scripts/apply_domain_schema.sh).
# SCRIPTS= below is kept for scripts/bundle_omics_sql.py (legacy sql_pg filenames).
#
# Example:
#   export PGHOST=localhost PGPORT=5432 PGDATABASE=goliath PGUSER=postgres PGPASSWORD='...'
#   bash /path/to/GoliathApp/workflow_engine/sql_pg/apply_schema.sh
#   bash ./apply_schema.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../scripts/domain_schema_order.sh
source "$REPO_ROOT/scripts/domain_schema_order.sh"

# Listed explicitly so scripts/bundle_omics_sql.py can parse apply order (must match
# DOMAIN_SCHEMA_LEGACY_NAMES in domain_schema_order.sh).
SCRIPTS=(
  portal_clinical_schema.sql
  portal_sample_extras_schema.sql
  legacy_cross_schema_fks.sql
  portal_clinical_api.sql
  portal_clinical_api_parity.sql
  portal_clinical_nav.sql
  cfg_analyte_catalog.sql
  omics_semantic_mvp.sql
  portal_semantic_row_types.sql
)

exec bash "$REPO_ROOT/scripts/apply_domain_schema.sh"
