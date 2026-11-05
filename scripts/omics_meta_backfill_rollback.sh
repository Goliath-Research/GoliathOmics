#!/usr/bin/env bash
# Roll back Meta mirror for one entity (legacy rows retained).
set -euo pipefail

ENTITY="${1:?entity_code required}"

CLEARED="$(psql -X -q -t -A -c "SELECT omics_clinical.sp_meta_rollback_entity('${ENTITY}')")"
echo "[INFO] entity=${ENTITY} meta_objects_removed=${CLEARED}"
