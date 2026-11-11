"""Register Omics Meta clinical classes (run after domain schema apply).

Idempotent: safe to re-run after ``scripts/apply_domain_schema.sh``.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

META_CLASS_SPECS: tuple[dict[str, Any], ...] = (
    {
        "entity_code": "disease",
        "class_name": "omics/Disease",
        "display_name": "Disease",
        "description": "Clinical disease or trait (Meta-backed; legacy portal.Diseases mirror).",
        "fields": [
            {
                "name": "legacy_disease_id",
                "type_name": "omics/meta/int",
                "type_kind": "int",
                "type_schema": {"type": "integer"},
                "required": False,
            },
            {
                "name": "name",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": "string"},
                "required": True,
            },
            {
                "name": "parent_id",
                "type_name": "omics/meta/int_nullable",
                "type_kind": "int",
                "type_schema": {"type": ["integer", "null"]},
                "required": False,
            },
        ],
    },
    {
        "entity_code": "customer",
        "class_name": "omics/Customer",
        "display_name": "Customer",
        "description": "Organization-scoped customer (Meta-backed; legacy portal.Customers mirror).",
        "fields": [
            {
                "name": "legacy_customer_id",
                "type_name": "omics/meta/int",
                "type_kind": "int",
                "type_schema": {"type": "integer"},
                "required": False,
            },
            {
                "name": "name",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": "string"},
                "required": True,
            },
            {
                "name": "organization_id",
                "type_name": "omics/meta/int",
                "type_kind": "int",
                "type_schema": {"type": "integer"},
                "required": True,
            },
        ],
    },
    {
        "entity_code": "patient",
        "class_name": "omics/Patient",
        "display_name": "Patient",
        "description": "Clinical patient (Meta-backed; legacy portal.Patients mirror).",
        "fields": [
            {
                "name": "legacy_patient_id",
                "type_name": "omics/meta/int",
                "type_kind": "int",
                "type_schema": {"type": "integer"},
                "required": False,
            },
            {
                "name": "institution_id",
                "type_name": "omics/meta/int_nullable",
                "type_kind": "int",
                "type_schema": {"type": ["integer", "null"]},
                "required": False,
            },
            {
                "name": "name",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": "string"},
                "required": True,
            },
            {
                "name": "sex",
                "type_name": "omics/meta/string_short",
                "type_kind": "string",
                "type_schema": {"type": ["string", "null"]},
                "required": False,
            },
            {
                "name": "year_born",
                "type_name": "omics/meta/int_nullable",
                "type_kind": "int",
                "type_schema": {"type": ["integer", "null"]},
                "required": False,
            },
            {
                "name": "race",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": ["string", "null"]},
                "required": False,
            },
            {
                "name": "ethnicity",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": ["string", "null"]},
                "required": False,
            },
            {
                "name": "external_id",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": ["string", "null"]},
                "required": False,
            },
        ],
    },
    {
        "entity_code": "sample",
        "class_name": "omics/Sample",
        "display_name": "Portal sample",
        "description": "Clinical sample (Meta-backed; legacy portal.Samples mirror).",
        "fields": [
            {
                "name": "legacy_sample_id",
                "type_name": "omics/meta/int",
                "type_kind": "int",
                "type_schema": {"type": "integer"},
                "required": False,
            },
            {
                "name": "patient_id",
                "type_name": "omics/meta/int",
                "type_kind": "int",
                "type_schema": {"type": "integer"},
                "required": True,
            },
            {
                "name": "customer_id",
                "type_name": "omics/meta/int_nullable",
                "type_kind": "int",
                "type_schema": {"type": ["integer", "null"]},
                "required": False,
            },
            {
                "name": "disease_id",
                "type_name": "omics/meta/int_nullable",
                "type_kind": "int",
                "type_schema": {"type": ["integer", "null"]},
                "required": False,
            },
            {
                "name": "sample_name",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": ["string", "null"]},
                "required": False,
            },
            {
                "name": "age",
                "type_name": "omics/meta/number",
                "type_kind": "number",
                "type_schema": {"type": ["number", "null"]},
                "required": False,
            },
            {
                "name": "bmi",
                "type_name": "omics/meta/number",
                "type_kind": "number",
                "type_schema": {"type": ["number", "null"]},
                "required": False,
            },
            {
                "name": "stage",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": ["string", "null"]},
                "required": False,
            },
        ],
    },
    {
        "entity_code": "lab_batch",
        "class_name": "omics/LabBatch",
        "display_name": "Lab batch",
        "description": "Processing lab batch (Meta-backed; legacy portal.Batches mirror).",
        "fields": [
            {
                "name": "legacy_batch_id",
                "type_name": "omics/meta/int",
                "type_kind": "int",
                "type_schema": {"type": "integer"},
                "required": False,
            },
            {
                "name": "name",
                "type_name": "omics/meta/string",
                "type_kind": "string",
                "type_schema": {"type": ["string", "null"]},
                "required": False,
            },
            {
                "name": "lab_id",
                "type_name": "omics/meta/int_nullable",
                "type_kind": "int",
                "type_schema": {"type": ["integer", "null"]},
                "required": False,
            },
        ],
    },
)

LEGACY_TABLE_NAMES: dict[str, str] = {
    "disease": "OmicsDisease",
    "customer": "OmicsCustomer",
    "patient": "OmicsPatient",
    "sample": "OmicsSample",
    "lab_batch": "OmicsLabBatch",
}


def _connect():
    import psycopg

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ.get("PGPASSWORD"),
    )


def seed(conn) -> int:
    count = 0
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        cur.execute("SELECT encode(digest('omics-meta-probe', 'sha256'), 'hex')")
        for spec in META_CLASS_SPECS:
            cur.execute(
                """
                SELECT omics_clinical.sp_meta_register_clinical_class(
                  %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    spec["entity_code"],
                    spec["class_name"],
                    spec["display_name"],
                    spec["description"],
                    json.dumps(spec["fields"]),
                ),
            )
            count += 1
        for entity_code, table_name in LEGACY_TABLE_NAMES.items():
            cur.execute(
                """
                UPDATE "Meta"."Classes" c
                SET "TableName" = %s
                FROM omics_clinical.meta_class_registry r
                WHERE r.entity_code = %s
                  AND c."ID" = r.class_id
                  AND c."TableName" IS DISTINCT FROM %s
                """,
                (table_name, entity_code, table_name),
            )
        cur.execute(
            """
            SELECT "Meta".sp_class_regenerate_views(r.class_id)
            FROM omics_clinical.meta_class_registry r
            """
        )
    conn.commit()
    return count


def main() -> int:
    if not os.environ.get("PGHOST"):
        print("Set PGHOST/PGDATABASE/PGUSER for goliath-omics-seed-meta-classes", file=sys.stderr)
        return 2
    with _connect() as conn:
        n = seed(conn)
    print(f"Registered {n} Omics Meta clinical classes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
