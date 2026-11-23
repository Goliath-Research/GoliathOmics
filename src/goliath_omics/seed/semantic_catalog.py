"""Seed portal.semantic_* registry from goliath_omics.semantic.catalog."""

from __future__ import annotations

import json
import os
import sys

from goliath_app.semantic.export import _schema_id, content_hash_for

from goliath_omics.semantic.catalog import SEMANTIC_CATALOG, SEMANTIC_REQUESTS
from goliath_omics.semantic.export import request_row_type


OMICS_NAMESPACE = "omics"
OMICS_OWNER_APP = "goliath_omics"


def _connect():
    import psycopg

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ.get("PGPASSWORD"),
    )


def _entity_by_code(code: str):
    for entity in SEMANTIC_CATALOG:
        if entity.code == code:
            return entity
    raise KeyError(f"unknown entity code {code}")


def _resolve_meta_class_id(cur, entity):
    if entity.meta_class_id is not None:
        return entity.meta_class_id
    # Meta storage class, else the owning class of an entity stored outside Meta.
    name = entity.meta_class_name or entity.owner_class_name
    if not name:
        return None
    cur.execute(
        """
        SELECT "ID" FROM "Meta"."Classes" WHERE "Name" = %s
        """,
        (name,),
    )
    row = cur.fetchone()
    return int(row[0]) if row is not None else None


def seed(conn) -> int:
    count = 0
    entity_ids: dict[str, int] = {}
    with conn.cursor() as cur:
        # GoliathOmics owns the omics namespace (display name kept if already set).
        cur.execute(
            """
            INSERT INTO portal.semantic_namespace (code, display_name, owner_app)
            VALUES (%s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET owner_app = EXCLUDED.owner_app
            """,
            (OMICS_NAMESPACE, OMICS_NAMESPACE, OMICS_OWNER_APP),
        )
        for entity in SEMANTIC_CATALOG:
            meta_class_id = _resolve_meta_class_id(cur, entity)
            row_schema = entity.row_model.model_json_schema(mode="serialization")
            entity_hash = content_hash_for(
                {
                    "namespace": entity.namespace,
                    "code": entity.code,
                    "schema_version": entity.schema_version,
                    "meta_class_id": entity.meta_class_id,
                    "meta_class_name": entity.meta_class_name,
                    **({"owner_class_name": entity.owner_class_name} if entity.owner_class_name else {}),
                    "row_schema": row_schema,
                    "attributes": [a.__dict__ for a in entity.attributes],
                }
            )
            cur.execute(
                """
                CALL portal.sp_semantic_register_entity(
                  %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    entity.namespace,
                    entity.code,
                    entity.display_name,
                    entity.description,
                    _schema_id(entity),
                    entity.schema_version,
                    entity_hash,
                    entity.storage_relation,
                    meta_class_id,
                ),
            )
            cur.execute(
                """
                SELECT id FROM portal.semantic_entity
                WHERE namespace = %s AND code = %s
                """,
                (entity.namespace, entity.code),
            )
            row = cur.fetchone()
            assert row is not None
            entity_ids[entity.code] = int(row[0])
            for attr in entity.attributes:
                cur.execute(
                    """
                    CALL portal.sp_semantic_register_attribute(
                      %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        entity_ids[entity.code],
                        attr.code,
                        attr.column_name,
                        attr.json_type,
                        attr.role,
                        list(attr.filterable_ops),
                    ),
                )
            count += 1

        for req in SEMANTIC_REQUESTS:
            entity = _entity_by_code(req.entity_code)
            params_schema = req.params_model.model_json_schema(mode="serialization")
            result_schema = req.result_row_model.model_json_schema(mode="serialization")
            filter_payload = (
                req.filter_template.model_dump(mode="json") if req.filter_template else None
            )
            req_hash = content_hash_for(
                {
                    "request_code": req.request_code,
                    "version": req.version,
                    "params_schema": params_schema,
                    "result_schema": result_schema,
                    "filter_template": filter_payload,
                }
            )
            row_type = request_row_type(req, entity)
            cur.execute(
                """
                CALL portal.sp_semantic_register_request(
                  %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s, %s
                )
                """,
                (
                    req.request_code,
                    req.version,
                    req.namespace,
                    entity_ids[req.entity_code],
                    req.target_kind,
                    req.target_name,
                    row_type,
                    json.dumps(params_schema),
                    json.dumps(result_schema),
                    req_hash,
                    json.dumps(filter_payload) if filter_payload is not None else None,
                    req.required_permission,
                    req.max_rows,
                    req.base_namespace,
                ),
            )
            count += 1
        ensure_request_permissions(cur)
        grant_entity_class_read(cur)
    conn.commit()
    return count


# Role that receives every permission an omics request requires, matching how GoliathApp's
# seed_platform_nav_tree.sql grants inv.stock.* to organization admins.
OMICS_REQUEST_PERMISSION_ROLE = "organization_admin"


def request_permissions() -> list[str]:
    """Distinct permissions named by omics semantic requests, in a stable order."""
    return sorted({req.required_permission for req in SEMANTIC_REQUESTS if req.required_permission})


def ensure_request_permissions(cur) -> None:
    """Register the permissions omics requests require and grant them to organization admins.

    portal.sp_semantic_run checks required_permission against RBAC.Permissions. Without these
    rows every omics.* request returns "permission denied" for every user. New permissions
    reuse the anchor (Meta object + meta.permission_anchor operation) that GoliathApp's
    platform permissions already use; the seed fails if that anchor does not exist yet.
    """
    names = request_permissions()
    if not names:
        return
    cur.execute(
        """
        SELECT p."ObjID", p."OperationID"
        FROM "RBAC"."Permissions" p
        INNER JOIN "Meta"."Operations" op ON op."ID" = p."OperationID"
        WHERE op."Name" = 'meta.permission_anchor'
        ORDER BY p."ID"
        LIMIT 1
        """
    )
    anchor = cur.fetchone()
    if not anchor:
        raise RuntimeError(
            "GoliathApp permission anchor missing; run GoliathApp seed_platform_nav_tree.sql before "
            "goliath-omics-seed-semantic"
        )
    obj_id, op_id = int(anchor[0]), int(anchor[1])
    for name in names:
        cur.execute(
            """
            INSERT INTO "RBAC"."Permissions" ("Name", "ObjID", "OperationID")
            SELECT %s, %s, %s
            WHERE NOT EXISTS (SELECT 1 FROM "RBAC"."Permissions" p WHERE p."Name" = %s)
            """,
            (name, obj_id, op_id, name),
        )
    cur.execute(
        """
        INSERT INTO "RBAC"."Role_Permissions" ("RoleID", "PermissionID")
        SELECT r."ID", p."ID"
        FROM "RBAC"."Roles" r
        INNER JOIN "RBAC"."Permissions" p ON p."Name" = ANY(%s)
        WHERE r."Name" = %s
          AND NOT EXISTS (
            SELECT 1 FROM "RBAC"."Role_Permissions" rp
            WHERE rp."RoleID" = r."ID" AND rp."PermissionID" = p."ID"
          )
        """,
        (names, OMICS_REQUEST_PERMISSION_ROLE),
    )
    cur.execute(
        "SELECT to_regprocedure('portal.sp_sync_god_architect_operator_entitlements()') IS NOT NULL"
    )
    row = cur.fetchone()
    if row and row[0]:
        cur.execute("SELECT portal.sp_sync_god_architect_operator_entitlements()")


def grant_entity_class_read(cur) -> None:
    """Keep access for roles that run omics requests now that entities map to Meta classes.

    Uses GoliathApp's grant (same transaction as the mapping) and fails the seed if any
    user/scope/request combination would still lose access.
    """
    cur.execute("SELECT to_regprocedure('\"Meta\".sp_grant_semantic_entity_class_read(text)') IS NOT NULL")
    row = cur.fetchone()
    if not (row and row[0]):
        return
    cur.execute('SELECT * FROM "Meta".sp_grant_semantic_entity_class_read(%s)', (OMICS_NAMESPACE,))
    cur.execute("SELECT count(*) FROM portal.fn_semantic_entity_class_read_gaps(%s)", (OMICS_NAMESPACE,))
    gaps = cur.fetchone()
    if gaps and int(gaps[0]) > 0:
        raise RuntimeError(f"{OMICS_NAMESPACE}: {gaps[0]} user/scope/request combinations would lose access")


def main() -> int:
    if not os.environ.get("PGHOST"):
        print("Set PGHOST/PGDATABASE/PGUSER for goliath-omics-seed-semantic", file=sys.stderr)
        return 2
    with _connect() as conn:
        n = seed(conn)
    print(f"Seeded {n} omics semantic registry objects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
