#!/usr/bin/env python3
"""Seed wf.workflow_action + wf.workflow_action_schema from goliath_app catalogs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKERS = REPO_ROOT / "workers"
WF_ENGINE = REPO_ROOT / "workflow_engine"

if str(WORKERS) not in sys.path:
    sys.path.insert(0, str(WORKERS))
if str(WF_ENGINE) not in sys.path:
    sys.path.insert(0, str(WF_ENGINE))

from goliath_app.actions.registry import load_action_catalog
from goliath_app.actions.schema_export import export_action_schemas
def _connect():
    import psycopg

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ.get("PGPASSWORD"),
    )


def _upsert_runtime_metadata(cur, entry) -> None:
    """Upsert CLI/dispatch metadata for one omics runtime catalog row."""
    dispatch = entry.dispatch.to_dict() if entry.dispatch else {}
    argv_map = {k: v for k, v in entry.argv_map} if entry.argv_map else None
    max_per_worker = dispatch.get("max_per_worker")
    affinity_key_field = dispatch.get("affinity_key_field")
    cur.execute(
        """
        CALL wf.wf_repo_upsert_workflow_action(
          %s, %s, %s, %s, %s, %s, %s::jsonb,
          %s, %s, %s, %s, %s, %s
        )
        """,
        (
            entry.action_name,
            entry.capability,
            entry.schema_id,
            entry.execution_mode,
            entry.cli_tool,
            entry.resolved_in_process_handler(),
            json.dumps(argv_map) if argv_map else None,
            int(max_per_worker) if max_per_worker is not None else None,
            bool(dispatch.get("exclusive_worker", False)),
            str(affinity_key_field) if affinity_key_field else None,
            bool(dispatch.get("prefer_previous_worker", False)),
            bool(dispatch.get("prefer_continue_group", False)),
            bool(dispatch.get("require_previous_worker", False)),
        ),
    )


def seed_semantic(conn) -> int:
    count = 0
    with conn.cursor() as cur:
        for spec in load_action_catalog():
            cur.execute(
                """
                CALL wf.wf_repo_upsert_semantic_action(
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    spec.action_name,
                    spec.resolved_capability,
                    spec.namespace,
                    spec.level.value,
                    spec.resolved_exposure.value,
                    spec.display_name,
                    spec.schema_version,
                    spec.overrides,
                    None,
                    "in_process" if spec.in_process_handler else "cli",
                    spec.in_process_handler,
                ),
            )
            for row in export_action_schemas(spec):
                cur.execute(
                    """
                    CALL wf.wf_repo_upsert_action_schema(
                      %s, %s, %s::jsonb, %s, %s, %s
                    )
                    """,
                    (
                        row.action_name,
                        row.direction,
                        json.dumps(row.schema_json),
                        row.schema_id,
                        row.schema_version,
                        row.content_hash,
                    ),
                )
            count += 1
    return count


def seed_omics_runtime(conn) -> int:
    from methyl_worker.action_runtime import RUNTIME_CATALOG

    count = 0
    with conn.cursor() as cur:
        for entry in RUNTIME_CATALOG:
            _upsert_runtime_metadata(cur, entry)
            count += 1
    return count


def seed(conn) -> tuple[int, int]:
    semantic = seed_semantic(conn)
    runtime = seed_omics_runtime(conn)
    conn.commit()
    return semantic, runtime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-db", action="store_true", help="Apply rows via libpq")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    args = parser.parse_args()

    catalog = load_action_catalog()
    if args.dry_run or not args.use_db:
        for spec in catalog:
            print(spec.action_name, spec.namespace, spec.level.value)
        return 0

    for key in ("PGHOST", "PGDATABASE", "PGUSER"):
        if not os.environ.get(key):
            print(f"missing required env {key}", file=sys.stderr)
            return 2

    with _connect() as conn:
        semantic, runtime = seed(conn)
    print(f"seeded {semantic} semantic action(s), {runtime} runtime metadata row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
