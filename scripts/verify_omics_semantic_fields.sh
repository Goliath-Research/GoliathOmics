#!/usr/bin/env bash
# Verify that every omics semantic attribute reads a field of its owning Meta class, that the
# 27 clinical fields stay on their omics/* classes, and that the reference fields are typed as
# relations. Prints one row per attribute. Exits 1 on any failure. Read-only.
#
#   export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...
#   ./scripts/verify_omics_semantic_fields.sh
set -euo pipefail

: "${PGHOST:?Set PGHOST}"
: "${PGDATABASE:?Set PGDATABASE}"

q() { psql -X -q -v ON_ERROR_STOP=1 -t -A -F '|' -c "$1"; }

echo "entity|attribute|column|owning_class|field_id|field_type|relation|status"
q "SELECT entity, attribute, column_name, coalesce(owning_class, '-'), coalesce(field_id::text, '-'),
          coalesce(field_type, '-'), coalesce(relation::text, '-'), status
   FROM omics_clinical.fn_semantic_attribute_fields()"

fail=0

missing=$(q "SELECT count(*) FROM omics_clinical.fn_semantic_attribute_fields() WHERE status = 'missing'")
if [[ "$missing" != "0" ]]; then
  echo "FAIL: $missing attribute(s) of Meta-stored omics entities have no field on their class" >&2
  fail=1
fi

# The 27 clinical fields, each on its omics/* class.
lost=$(q "SELECT string_agg(x.cls || '.' || x.fld, ', ')
  FROM (VALUES
    ('omics/Institution','legacy_institution_id'),('omics/Institution','name'),
    ('omics/Customer','legacy_customer_id'),('omics/Customer','name'),('omics/Customer','organization_id'),
    ('omics/Disease','legacy_disease_id'),('omics/Disease','name'),('omics/Disease','parent_id'),
    ('omics/LabBatch','legacy_batch_id'),('omics/LabBatch','name'),('omics/LabBatch','lab_id'),
    ('omics/Patient','legacy_patient_id'),('omics/Patient','institution_id'),('omics/Patient','name'),
    ('omics/Patient','sex'),('omics/Patient','year_born'),('omics/Patient','race'),
    ('omics/Patient','ethnicity'),('omics/Patient','external_id'),
    ('omics/Sample','legacy_sample_id'),('omics/Sample','patient_id'),('omics/Sample','customer_id'),
    ('omics/Sample','disease_id'),('omics/Sample','sample_name'),('omics/Sample','age'),
    ('omics/Sample','bmi'),('omics/Sample','stage')
  ) AS x(cls, fld)
  WHERE NOT EXISTS (
    SELECT 1 FROM \"Meta\".\"Fields\" f JOIN \"Meta\".\"Classes\" c ON c.\"ID\" = f.\"ClassID\"
    WHERE c.\"Name\" = x.cls AND f.\"Name\" = x.fld)")
if [[ -n "$lost" ]]; then
  echo "FAIL: clinical fields missing from their omics/* class: $lost" >&2
  fail=1
fi

# Reference fields typed as relations.
untyped=$(q "SELECT string_agg(x.cls || '.' || x.fld, ', ')
  FROM (VALUES
    ('omics/Sample','patient_id'),('omics/Sample','customer_id'),('omics/Sample','disease_id'),
    ('omics/Patient','institution_id'),('omics/LabBatch','lab_id'),
    ('omics/Customer','organization_id'),('omics/Disease','parent_id')
  ) AS x(cls, fld)
  WHERE NOT EXISTS (
    SELECT 1 FROM \"Meta\".\"Fields\" f
    JOIN \"Meta\".\"Classes\" c ON c.\"ID\" = f.\"ClassID\"
    JOIN wf.data_type t ON t.id = f.\"TypeID\"
    WHERE c.\"Name\" = x.cls AND f.\"Name\" = x.fld AND t.schema_json ? 'x-goliath-relation')")
if [[ -n "$untyped" ]]; then
  echo "FAIL: reference fields not typed as relations: $untyped" >&2
  fail=1
fi

inst_rel=$(q "SELECT t.schema_json->'x-goliath-relation'->>'class'
  FROM \"Meta\".\"Fields\" f
  JOIN \"Meta\".\"Classes\" c ON c.\"ID\" = f.\"ClassID\"
  JOIN wf.data_type t ON t.id = f.\"TypeID\"
  WHERE c.\"Name\" = 'omics/Patient' AND f.\"Name\" = 'institution_id'")
if [[ "$inst_rel" != "omics/Institution" ]]; then
  echo "FAIL: omics/Patient.institution_id must relate to omics/Institution (got ${inst_rel:-missing})" >&2
  fail=1
fi

legacy=$(q "SELECT count(*) FROM \"Meta\".\"Classes\"
  WHERE \"Name\" IN ('OmicsDisease','OmicsCustomer','OmicsPatient','OmicsSample','OmicsLabBatch')")
echo "attributes per entity: $(q "SELECT string_agg(entity || '=' || n, ' ') FROM (
  SELECT entity, count(*) n FROM omics_clinical.fn_semantic_attribute_fields() GROUP BY 1 ORDER BY 1) s")"
echo "legacy Omics* classes remaining: $legacy"

if [[ "$fail" != "0" ]]; then
  exit 1
fi
echo "OK: omics semantic attributes, clinical fields and relations verified"
