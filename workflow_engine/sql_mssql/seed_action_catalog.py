#!/usr/bin/env python3
"""
Seed wf.workflow_action rows from the unified action catalog, then seed
wf.data_type (+ action input/output type FKs) via seed_data_types.py.

Legacy per-action JSON Schema blobs (wf.workflow_action_schema) are no longer
seeded; types are explicit rows in wf.data_type / wf.data_type_field, with the
JSON Schema document stored on wf.data_type.schema_json for SchemaPropertyGrid.

Uses the gateway DB layer (Azure SQL or PostgreSQL) via BACKEND_DB / connection env.
Legacy PostgreSQL-only path: pass --dsn postgresql://...

Usage:
  source .venv/bin/activate
  methyl-export-task-schemas
  methyl-export-action-catalog
  python workflow_engine/sql_mssql/seed_action_catalog.py

Azure SQL (default BACKEND_DB=mssql):
  export BACKEND_DB=mssql
  export AZURE_SQL_SERVER=...
  export AZURE_SQL_DB=...
  export AZURE_SQL_USER=...
  export AZURE_SQL_PASSWORD=...
  python workflow_engine/sql_mssql/seed_action_catalog.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "schemas" / "actions" / "catalog.json"
TASKS_DIR = REPO_ROOT / "schemas" / "tasks"
WF_ENGINE = REPO_ROOT / "workflow_engine"

sys.path.insert(0, str(REPO_ROOT / "workers"))
sys.path.insert(0, str(WF_ENGINE))

from methyl_worker.action_catalog_export import export_action_catalog  # noqa: E402
from methyl_worker.task_schema_registry import list_task_schema_specs  # noqa: E402


def pg_dsn() -> str:
    import os

    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "methylpipeline_parity")
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "methyl")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json_literal(obj: dict) -> str:
    escaped = json.dumps(obj, separators=(",", ":")).replace("'", "''")
    return f"'{escaped}'::jsonb"


def _exec_psql(dsn: str, sql: str) -> None:
    subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
    )


def _upsert_action_psql(
    dsn: str,
    action_name: str,
    capability: str,
    payload_schema_ref: str | None,
    *,
    execution_mode: str | None = None,
    cli_tool: str | None = None,
    in_process_handler: str | None = None,
    argv_map: dict | None = None,
    max_per_worker: int | None = None,
    exclusive_worker: bool = False,
    affinity_key_field: str | None = None,
    prefer_previous_worker: bool = False,
    prefer_continue_group: bool = False,
) -> None:
    """Upsert via 12-arg ``wf_repo_upsert_workflow_action`` (dispatch + affinity)."""
    cap = _sql_literal(capability) if capability else "NULL"
    ref = _sql_literal(payload_schema_ref) if payload_schema_ref else "NULL"
    mode = _sql_literal(execution_mode) if execution_mode else "NULL"
    tool = _sql_literal(cli_tool) if cli_tool else "NULL"
    handler = _sql_literal(in_process_handler) if in_process_handler else "NULL"
    argv = _json_literal(argv_map) if isinstance(argv_map, dict) else "NULL"
    max_pw = str(int(max_per_worker)) if max_per_worker is not None else "NULL"
    excl = "true" if exclusive_worker else "false"
    aff = _sql_literal(affinity_key_field) if affinity_key_field else "NULL"
    pref_prev = "true" if prefer_previous_worker else "false"
    pref_cont = "true" if prefer_continue_group else "false"
    _exec_psql(
        dsn,
        "CALL wf.wf_repo_upsert_workflow_action("
        f"{_sql_literal(action_name)}, {cap}, {ref}, {mode}, {tool}, {handler}, {argv}, "
        f"{max_pw}, {excl}, {aff}, {pref_prev}, {pref_cont});",
    )


def _upsert_schema_psql(
    dsn: str, action_name: str, direction: str, schema: dict, schema_id: str
) -> None:
    _exec_psql(
        dsn,
        "CALL wf.wf_repo_upsert_action_schema("
        f"{_sql_literal(action_name)}, "
        f"{_sql_literal(direction)}, "
        f"{_json_literal(schema)}, "
        f"{_sql_literal(schema_id)});",
    )


def _seed_via_db() -> tuple[int, int]:
    from rest.connection import resolve_connection_config
    from rest.db import open_gateway_db
    from rest.db_client import upsert_workflow_action

    config = resolve_connection_config()
    db = open_gateway_db(config)
    try:
        if not CATALOG_PATH.is_file():
            raise SystemExit(f"Missing {CATALOG_PATH}; run methyl-export-action-catalog first.")

        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        actions = catalog.get("actions") or []

        action_count = 0
        for action in actions:
            dispatch = action.get("dispatch") if isinstance(action.get("dispatch"), dict) else {}
            max_per_worker = dispatch.get("max_per_worker")
            affinity_key_field = dispatch.get("affinity_key_field")
            upsert_workflow_action(
                db,
                str(action["action_name"]),
                str(action.get("capability") or "") or None,
                str(action.get("schema_id") or action["action_name"]),
                execution_mode=action.get("execution_mode"),
                cli_tool=action.get("cli_tool"),
                in_process_handler=action.get("in_process_handler"),
                argv_map=action.get("argv_map") if isinstance(action.get("argv_map"), dict) else None,
                max_per_worker=int(max_per_worker) if max_per_worker is not None else None,
                exclusive_worker=bool(dispatch.get("exclusive_worker", False)),
                affinity_key_field=str(affinity_key_field) if affinity_key_field else None,
                prefer_previous_worker=bool(dispatch.get("prefer_previous_worker", False)),
                prefer_continue_group=bool(dispatch.get("prefer_continue_group", False)),
            )
            action_count += 1
            print(f"Upserted action {action['action_name']}")

        return action_count, 0
    finally:
        db.close()


def _seed_via_psql(dsn: str) -> tuple[int, int]:
    if not CATALOG_PATH.is_file():
        raise SystemExit(f"Missing {CATALOG_PATH}; run methyl-export-action-catalog first.")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    actions = catalog.get("actions") or []

    action_count = 0
    for action in actions:
        dispatch = action.get("dispatch") if isinstance(action.get("dispatch"), dict) else {}
        max_per_worker = dispatch.get("max_per_worker")
        affinity_key_field = dispatch.get("affinity_key_field")
        _upsert_action_psql(
            dsn,
            str(action["action_name"]),
            str(action.get("capability") or ""),
            str(action.get("schema_id") or action["action_name"]),
            execution_mode=action.get("execution_mode"),
            cli_tool=action.get("cli_tool"),
            in_process_handler=action.get("in_process_handler"),
            argv_map=action.get("argv_map") if isinstance(action.get("argv_map"), dict) else None,
            max_per_worker=int(max_per_worker) if max_per_worker is not None else None,
            exclusive_worker=bool(dispatch.get("exclusive_worker", False)),
            affinity_key_field=str(affinity_key_field) if affinity_key_field else None,
            prefer_previous_worker=bool(dispatch.get("prefer_previous_worker", False)),
            prefer_continue_group=bool(dispatch.get("prefer_continue_group", False)),
        )
        action_count += 1
        print(f"Upserted action {action['action_name']}")

    return action_count, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed wf.workflow_action + wf.data_type from schemas/actions/catalog.json"
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "PostgreSQL DSN only (skip gateway env; uses psql). "
            "Requires wf_action_dispatch_affinity.sql so the 12-arg upsert exists."
        ),
    )
    parser.add_argument(
        "--regenerate-catalog",
        action="store_true",
        help="Run methyl-export-action-catalog before seeding",
    )
    parser.add_argument(
        "--use-db",
        action="store_true",
        help="Seed via direct DB connection (default when --dsn is not set)",
    )
    args = parser.parse_args(argv)

    if args.regenerate_catalog:
        export_action_catalog(write=True)

    if args.dsn:
        action_count, _schema_count = _seed_via_psql(args.dsn)
    else:
        action_count, _schema_count = _seed_via_db()

    print(f"Seeded {action_count} action(s).")

    # Explicit data types + action input/output FKs (schema_json on wf.data_type).
    seed_dt = REPO_ROOT / "workflow_engine" / "sql_mssql" / "seed_data_types.py"
    if seed_dt.is_file() and not args.dsn:
        proc = subprocess.run(
            [sys.executable, str(seed_dt)],
            cwd=str(REPO_ROOT),
            check=False,
        )
        if proc.returncode != 0:
            print("warn: seed_data_types.py failed", file=sys.stderr)
            return proc.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
