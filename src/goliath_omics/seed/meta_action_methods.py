"""Bind wf.workflow_action rows to Meta class methods (ImplKind = action)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENTITY_META_CLASS: dict[str, str] = {
    "disease": "omics/Disease",
    "customer": "omics/Customer",
    "institution": "omics/Institution",
    "patient": "omics/Patient",
    "sample": "omics/Sample",
    "lab_batch": "omics/LabBatch",
    "pipeline": "omics/Study",
    "validation": "omics/Study",
    "workflow": "omics/Study",
    "context": "omics/Study",
    "stock_lot": "omics/LabBatch",
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


def _catalog_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / "schemas" / "actions" / "catalog.json"


def _platform_owned(cur, action_name: str) -> bool:
    """True when a platform/* class already owns the action (each action has one owning class)."""
    cur.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM "Meta"."Methods" m JOIN "Meta"."Classes" c ON c."ID" = m."ClassID"
          WHERE m."ImplKind" = 'action' AND m."ImplRef" = %s AND c."Name" LIKE 'platform/%%'
        )
        """,
        (action_name,),
    )
    row = cur.fetchone()
    return bool(row and row[0])


def derive_methods(conn) -> list[tuple]:
    """Typed parameters and semantic bindings for omics action methods (GoliathApp derivation step).

    Returns the rows that need attention (no_owner, multiple_owners, no_input_schema, ambiguous).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regprocedure('\"Meta\".sp_derive_action_methods(text,boolean)') IS NOT NULL")
        row = cur.fetchone()
        if not (row and row[0]):
            return []
        cur.execute(
            """
            SELECT action_name, owner_class, status, param_count, semantic_entity, detail
            FROM "Meta".sp_derive_action_methods('omics', true)
            WHERE status NOT IN ('derived', 'typed')
            """
        )
        issues = list(cur.fetchall())
    conn.commit()
    return issues


_PLATFORM_DUPLICATES_SQL = """
    SELECT m."ID", c."Name", m."Name", m."ImplRef"
    FROM "Meta"."Methods" m
    JOIN "Meta"."Classes" c ON c."ID" = m."ClassID"
    WHERE m."ImplKind" = 'action'
      AND c."Name" LIKE 'omics/%%'
      AND NOT COALESCE(m."IsOverride", false)
      AND EXISTS (
        SELECT 1 FROM "Meta"."Methods" p JOIN "Meta"."Classes" pc ON pc."ID" = p."ClassID"
        WHERE p."ImplKind" = 'action' AND p."ImplRef" = m."ImplRef"
          AND pc."Name" LIKE 'platform/%%' AND NOT COALESCE(p."IsOverride", false)
      )
    ORDER BY m."ImplRef", c."Name"
"""


def platform_duplicates(conn) -> list[tuple]:
    """omics/* methods for actions a platform/* class already owns: (id, class, method, action)."""
    with conn.cursor() as cur:
        cur.execute(_PLATFORM_DUPLICATES_SQL)
        return list(cur.fetchall())


def remove_platform_duplicates(conn, *, expect: int) -> list[tuple]:
    """Delete the omics/* duplicates of platform-owned action methods (parameters cascade).

    The platform class stays the action's only owner. Nothing is deleted unless exactly
    ``expect`` duplicates are found, so a changed catalog never deletes more than was reviewed.
    """
    rows = platform_duplicates(conn)
    if len(rows) != expect:
        raise SystemExit(f"expected {expect} platform duplicates, found {len(rows)}; nothing deleted")
    with conn.cursor() as cur:
        for method_id, _cls, _name, _action in rows:
            cur.execute('DELETE FROM "Meta"."Methods" WHERE "ID" = %s', (method_id,))
    conn.commit()
    return rows


def seed(conn) -> int:
    catalog = json.loads(_catalog_path().read_text(encoding="utf-8"))
    actions = catalog.get("actions") or {}
    count = 0
    with conn.cursor() as cur:
        for action_name, spec in sorted(actions.items()):
            entity_code = spec.get("entity_code")
            class_name = ENTITY_META_CLASS.get(entity_code or "")
            if not class_name or _platform_owned(cur, action_name):
                continue
            method_name = action_name.split(".", 1)[-1]
            description = spec.get("description") or spec.get("display_name")
            cur.execute(
                """
                SELECT "Meta".sp_meta_upsert_action_method(%s, %s, %s, %s)
                """,
                (class_name, method_name, action_name, description),
            )
            count += 1
    conn.commit()
    return count


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Bind omics actions to Meta class methods")
    parser.add_argument(
        "--remove-platform-duplicates",
        type=int,
        metavar="N",
        help="Delete the N omics/* methods duplicating platform-owned actions (then exit)",
    )
    args = parser.parse_args(argv)
    if not os.environ.get("PGHOST"):
        print(
            "Set PGHOST/PGDATABASE/PGUSER for goliath-omics-seed-meta-action-methods",
            file=sys.stderr,
        )
        return 2
    if args.remove_platform_duplicates is not None:
        with _connect() as conn:
            removed = remove_platform_duplicates(conn, expect=args.remove_platform_duplicates)
        for method_id, cls, name, action in removed:
            print(f"removed {cls}.{name} (method {method_id}) duplicating {action}")
        return 0
    with _connect() as conn:
        bound = seed(conn)
        issues = derive_methods(conn)
    print(f"Bound {bound} workflow actions to Meta class methods")
    for action_name, owner, status, _count, _entity, detail in issues:
        print(f"  {status}: {action_name} ({owner or 'no owner'}) {detail or ''}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
