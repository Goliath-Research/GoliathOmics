"""Idempotent Omics NavTree registration via portal.sp_nav_register_node."""

from __future__ import annotations

import os
import sys


def _connect():
    import psycopg

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ.get("PGPASSWORD"),
    )


NAV_SPEC = (
    ("Omics", None, 15, "omics_root", "omics"),
    ("Clinical", "Omics", 10, "omics_clinical_hub", "omics.disease.list"),
    ("Diseases", "Clinical", 10, "omics_diseases", "omics.disease.list"),
    ("Customers", "Clinical", 20, "omics_customers", "omics.customer.list"),
    ("Groups", "Clinical", 30, "omics_groups", "omics.group.list"),
    ("Samples", "Clinical", 40, "omics_samples", "omics.sample.list"),
    ("Study enrollment", "Clinical", 50, "omics_enrollment", "omics.group.list_for_enrollment"),
    ("Labs", "Omics", 20, "omics_labs", "omics.lab.list"),
)


def _ensure_node_info(cur, name: str, frame_key: str) -> int:
    cur.execute(
        """
        SELECT "Id" FROM portal."NodeInfos" WHERE "Name" = %s
        """,
        (name,),
    )
    row = cur.fetchone()
    if row:
        info_id = int(row[0])
        cur.execute(
            """
            UPDATE portal."NodeInfos"
            SET "FrameKey" = %s
            WHERE "Id" = %s AND "FrameKey" IS DISTINCT FROM %s
            """,
            (frame_key, info_id, frame_key),
        )
        return info_id
    cur.execute(
        """
        INSERT INTO portal."NodeInfos" ("Name", "Singleton", "TypeId", "FrameKey")
        VALUES (%s, true, 1, %s)
        RETURNING "Id"
        """,
        (name, frame_key),
    )
    return int(cur.fetchone()[0])


def _find_node(cur, caption: str, parent_id: int | None) -> int | None:
    if parent_id is None:
        cur.execute(
            """
            SELECT "ID" FROM portal."NavTree"
            WHERE "Caption" = %s AND "ParentID" IS NULL
            """,
            (caption,),
        )
    else:
        cur.execute(
            """
            SELECT "ID" FROM portal."NavTree"
            WHERE "Caption" = %s AND "ParentID" = %s
            """,
            (caption, parent_id),
        )
    row = cur.fetchone()
    return int(row[0]) if row else None


def seed(conn) -> int:
    created = 0
    node_ids: dict[str, int] = {}
    with conn.cursor() as cur:
        for caption, parent_caption, seq, info_name, frame_key in NAV_SPEC:
            parent_id = node_ids.get(parent_caption) if parent_caption else None
            info_id = _ensure_node_info(cur, info_name, frame_key or info_name)
            existing = _find_node(cur, caption, parent_id)
            if existing is not None:
                node_ids[caption] = existing
                continue
            cur.execute(
                """
                SELECT node_id FROM portal.sp_nav_register_node(%s, %s, %s::smallint, %s)
                """,
                (parent_id, caption, seq, info_id),
            )
            node_ids[caption] = int(cur.fetchone()[0])
            created += 1
    conn.commit()
    return created


def main() -> int:
    if not os.environ.get("PGHOST"):
        print("Set PGHOST/PGDATABASE/PGUSER for goliath-omics-seed-nav", file=sys.stderr)
        return 2
    with _connect() as conn:
        n = seed(conn)
    print(f"Registered {n} new Omics NavTree nodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
