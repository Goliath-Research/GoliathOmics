#!/usr/bin/env bash
# Verify Meta backfill counts and legacy checksum for one entity.
set -euo pipefail

ENTITY="${1:?entity_code required}"

psql -X -q -c "
SELECT
  '${ENTITY}' AS entity_code,
  omics_clinical.fn_meta_backfill_count('${ENTITY}') AS rows_missing_meta,
  omics_clinical.fn_meta_legacy_checksum('${ENTITY}') AS legacy_checksum,
  f.dual_write_legacy_meta,
  f.read_from_meta
FROM omics_clinical.meta_cutover_flag f
WHERE f.entity_code = '${ENTITY}';
"
