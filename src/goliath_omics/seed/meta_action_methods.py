"""Bind wf.workflow_action rows to Meta class methods (ImplKind = action)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENTITY_META_CLASS: dict[str, str] = {
    "disease": "omics/Disease",
    "customer": "omics/Customer",
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


def seed(conn) -> int:
    catalog = json.loads(_catalog_path().read_text(encoding="utf-8"))
    actions = catalog.get("actions") or {}
    count = 0
    with conn.cursor() as cur:
        for action_name, spec in sorted(actions.items()):
            entity_code = spec.get("entity_code")
            class_name = ENTITY_META_CLASS.get(entity_code or "")
            if not class_name:
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


def main() -> int:
    if not os.environ.get("PGHOST"):
        print(
            "Set PGHOST/PGDATABASE/PGUSER for goliath-omics-seed-meta-action-methods",
            file=sys.stderr,
        )
        return 2
    with _connect() as conn:
        bound = seed(conn)
    print(f"Bound {bound} workflow actions to Meta class methods")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
