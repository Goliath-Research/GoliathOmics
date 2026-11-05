#!/usr/bin/env bash
# Canonical GoliathOmics domain DDL apply order.
# Source this file from apply_domain_schema.sh and workflow_engine/sql_pg/apply_schema.sh.
#
# Canonical files live under database/schema/. workflow_engine/sql_pg/ keeps legacy
# filenames as symlinks for one release cycle (bundle_omics_sql.py, omics-sql/).

DOMAIN_SCHEMA_SCRIPTS=(
  01_portal_clinical_schema.sql
  02_portal_sample_extras_schema.sql
  03_legacy_cross_schema_fks.sql
  04_portal_clinical_api.sql
  05_portal_clinical_api_parity.sql
  06_portal_clinical_nav.sql
  07_cfg_analyte_catalog.sql
  08_omics_semantic_mvp.sql
  09_portal_semantic_row_types.sql
  10_omics_meta_clinical_classes.sql
  11_omics_meta_semantic_writes.sql
)

# Legacy sql_pg names (same order); used by release bundles and apply_schema.sh SCRIPTS=.
DOMAIN_SCHEMA_LEGACY_NAMES=(
  portal_clinical_schema.sql
  portal_sample_extras_schema.sql
  legacy_cross_schema_fks.sql
  portal_clinical_api.sql
  portal_clinical_api_parity.sql
  portal_clinical_nav.sql
  cfg_analyte_catalog.sql
  omics_semantic_mvp.sql
  portal_semantic_row_types.sql
  omics_meta_clinical_classes.sql
  omics_meta_semantic_writes.sql
)

if ((${#DOMAIN_SCHEMA_SCRIPTS[@]} != ${#DOMAIN_SCHEMA_LEGACY_NAMES[@]})); then
  echo "domain_schema_order.sh: canonical and legacy name lists differ in length" >&2
  exit 1
fi
