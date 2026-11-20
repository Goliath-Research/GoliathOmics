#!/usr/bin/env bash
# Verify that Omics config document types are registered for the generic editors.
# Checks cfg kind pipeline_profile -> omics/PipelineProfile and site -> omics/SiteParameters,
# and that both wf.data_type rows are published. Exits 1 on any failure. Read-only.
#
#   export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...
#   ./scripts/verify_omics_config_document_types.sh
set -euo pipefail

: "${PGHOST:?Set PGHOST}"
: "${PGDATABASE:?Set PGDATABASE}"

q() { psql -X -q -v ON_ERROR_STOP=1 -t -A -F '|' -c "$1"; }

fail=0

if [[ "$(q "SELECT to_regprocedure('cfg.sp_register_kind_document_type(text,text)') IS NOT NULL")" != "t" ]]; then
  echo "FAIL: cfg.sp_register_kind_document_type is not installed" >&2
  exit 1
fi

echo "kind|type_name|data_type_status"
q "SELECT d.kind, t.name, coalesce(t.status, '-')
   FROM cfg.kind_document_type d
   LEFT JOIN wf.data_type t ON t.id = d.type_id
   WHERE d.kind IN ('pipeline_profile', 'site')
   ORDER BY d.kind"

missing=$(q "SELECT string_agg(wanted.kind, ', ')
  FROM (VALUES
    ('pipeline_profile', 'omics/PipelineProfile'),
    ('site', 'omics/SiteParameters')
  ) AS wanted(kind, type_name)
  LEFT JOIN cfg.kind_document_type d ON d.kind = wanted.kind
  LEFT JOIN wf.data_type t ON t.id = d.type_id AND t.name = wanted.type_name
  WHERE t.name IS NULL")
if [[ -n "$missing" ]]; then
  echo "FAIL: unregistered or mistyped kind(s): $missing" >&2
  fail=1
fi

unpublished=$(q "SELECT string_agg(wanted.type_name, ', ')
  FROM (VALUES
    ('omics/PipelineProfile'),
    ('omics/SiteParameters')
  ) AS wanted(type_name)
  LEFT JOIN wf.data_type t ON t.name = wanted.type_name AND t.status = 'published'
  WHERE t.name IS NULL")
if [[ -n "$unpublished" ]]; then
  echo "FAIL: data type(s) not published: $unpublished" >&2
  fail=1
fi

if [[ "$fail" -eq 0 ]]; then
  echo "OK: site and pipeline_profile document types are registered and published"
fi
exit "$fail"
