#!/usr/bin/env bash
# Record omics/SequencingRun and omics/AlignmentResult Meta objects for every succeeded source
# step already in wf.node_execution (database/schema/22_omics_run_alignment_objects.sql).
# Only real rows are read; nothing is invented. Rerunning is a no-op.
#
# Requires PGHOST, PGDATABASE, PGUSER and PGPASSWORD (or PGPASSFILE).
#
# Usage:
#   ./scripts/omics_run_alignment_backfill.sh           # dry run (one transaction, rolled back)
#   ./scripts/omics_run_alignment_backfill.sh --apply   # commit
set -euo pipefail

MODE="${1:-}"
case "$MODE" in
  ""|--dry-run) ACTION=ROLLBACK ;;
  --apply) ACTION=COMMIT ;;
  *) echo "usage: $0 [--dry-run|--apply]" >&2; exit 2 ;;
esac
command -v psql >/dev/null 2>&1 || { echo "psql not on PATH" >&2; exit 1; }

q() { psql -X -q -t -A -v ON_ERROR_STOP=1 -c "$1"; }

[[ "$(q "SELECT to_regprocedure('omics_clinical.sp_run_alignment_backfill()') IS NOT NULL")" == "t" ]] \
  || { echo "[ERROR] apply 22_omics_run_alignment_objects.sql first (scripts/apply_domain_schema.sh)" >&2; exit 1; }

echo "[INFO] pending before: $(q "SELECT COALESCE(string_agg(class_name || '=' || n, ' '), 'none') FROM (
  SELECT class_name, count(*) n FROM omics_clinical.v_run_alignment_pending GROUP BY 1 ORDER BY 1) s")"

psql -X -q -v ON_ERROR_STOP=1 -t -A -F ' ' <<SQL
BEGIN;
SELECT '[INFO] ' || class_name || ': source_rows=' || source_rows || ' created=' || created || ' updated=' || updated
FROM omics_clinical.sp_run_alignment_backfill();
SELECT '[INFO] pending after: ' || count(*) FROM omics_clinical.v_run_alignment_pending;
DO \$check\$
BEGIN
  IF EXISTS (SELECT 1 FROM omics_clinical.v_run_alignment_pending) THEN
    RAISE EXCEPTION 'backfill left succeeded source rows without an object';
  END IF;
END
\$check\$;
$ACTION;
SQL
if [[ "$ACTION" == "ROLLBACK" ]]; then
  echo "[INFO] dry run: rolled back (pass --apply to commit)"
else
  echo "[INFO] committed"
fi
