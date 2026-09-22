#!/usr/bin/env python3
"""
Upsert schemas/tasks/*.schema.json into wf.workflow_action_schema.

Requires PostgreSQL wf schema with wf_action_schema.sql deployed.

Usage:
  python workflow_engine/sql_mssql/seed_action_schemas.py
  python workflow_engine/sql_mssql/seed_action_schemas.py --dsn postgresql://...
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "schemas" / "tasks"

sys.path.insert(0, str(REPO_ROOT / "workers"))

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


def upsert_schema(dsn: str, action_name: str, direction: str, schema: dict, schema_id: str) -> None:
    sql = (
        "CALL wf.wf_repo_upsert_action_schema("
        f"{_sql_literal(action_name)}, "
        f"{_sql_literal(direction)}, "
        f"{_json_literal(schema)}, "
        f"{_sql_literal(schema_id)});"
    )
    subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed wf.workflow_action_schema from schemas/tasks/")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN (default: POSTGRES_* env)")
    args = parser.parse_args(argv)
    dsn = args.dsn or pg_dsn()

    if not TASKS_DIR.is_dir():
        print(f"Missing {TASKS_DIR}; run methyl-export-task-schemas first.", file=sys.stderr)
        return 1

    count = 0
    for spec in list_task_schema_specs():
        for direction, filename in (
            ("input", spec.input_filename),
            ("output", spec.output_filename),
        ):
            path = TASKS_DIR / filename
            if not path.is_file():
                print(f"Skip missing {path}", file=sys.stderr)
                continue
            schema = json.loads(path.read_text(encoding="utf-8"))
            upsert_schema(dsn, spec.action_name, direction, schema, spec.schema_id)
            count += 1
            print(f"Upserted {spec.action_name} ({direction})")

    print(f"Seeded {count} action schema(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
