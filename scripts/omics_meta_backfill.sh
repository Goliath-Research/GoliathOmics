#!/usr/bin/env bash
# Backfill legacy portal clinical rows into Meta for one entity (R4).
# Requires: PGHOST, PGDATABASE, PGUSER, PGPASSWORD (or PGPASSFILE).
#
# Usage:
#   ./scripts/omics_meta_backfill.sh disease
#   ./scripts/omics_meta_backfill.sh customer --enable-dual-write

set -euo pipefail

ENTITY="${1:?entity_code required: disease|customer|patient|sample|lab_batch}"
ENABLE_DUAL="${2:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v psql >/dev/null 2>&1; then
  echo "psql not on PATH" >&2
  exit 1
fi

BEFORE_COUNT="$(psql -X -q -t -A -c "SELECT omics_clinical.fn_meta_backfill_count('${ENTITY}')")"
BEFORE_SUM="$(psql -X -q -t -A -c "SELECT omics_clinical.fn_meta_legacy_checksum('${ENTITY}')")"

echo "[INFO] entity=${ENTITY} pending_rows=${BEFORE_COUNT} legacy_checksum=${BEFORE_SUM}"

MIRRORED="$(psql -X -q -t -A -c "SELECT omics_clinical.sp_meta_backfill_entity('${ENTITY}')")"
AFTER_COUNT="$(psql -X -q -t -A -c "SELECT omics_clinical.fn_meta_backfill_count('${ENTITY}')")"
AFTER_SUM="$(psql -X -q -t -A -c "SELECT omics_clinical.fn_meta_legacy_checksum('${ENTITY}')")"

if [[ "${BEFORE_SUM}" != "${AFTER_SUM}" ]]; then
  echo "[ERROR] legacy checksum changed during backfill (before=${BEFORE_SUM} after=${AFTER_SUM})" >&2
  exit 1
fi

if [[ "${AFTER_COUNT}" != "0" ]]; then
  echo "[ERROR] backfill incomplete: ${AFTER_COUNT} rows still missing MetaObjectID" >&2
  exit 1
fi

echo "[INFO] mirrored_rows=${MIRRORED} legacy_checksum=${AFTER_SUM}"

if [[ "${ENABLE_DUAL}" == "--enable-dual-write" ]]; then
  psql -X -q -c "CALL omics_clinical.sp_meta_set_cutover_flag('${ENTITY}', true, false)"
  echo "[INFO] dual_write_legacy_meta=true read_from_meta=false for ${ENTITY}"
fi
