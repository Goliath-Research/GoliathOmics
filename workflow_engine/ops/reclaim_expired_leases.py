"""CLI: reclaim RUNNING node_executions with expired/missing leases."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from rest.connection import DatabaseBackend, resolve_connection_config


def reclaim_expired_leases(
    *,
    workflow_instance_id: Optional[int] = None,
    grace_seconds: int = 60,
) -> list[dict[str, Any]]:
    """Execute ``wf.sp_reclaim_expired_leases`` and return reclaimed rows."""
    config = resolve_connection_config()
    if config.backend == DatabaseBackend.MSSQL:
        return _reclaim_mssql(config, workflow_instance_id, grace_seconds)
    if config.backend == DatabaseBackend.POSTGRES:
        return _reclaim_postgres(config, workflow_instance_id, grace_seconds)
    raise SystemExit(
        f"reclaim_expired_leases is not implemented for backend={config.backend.value}"
    )


def _reclaim_mssql(
    config: Any,
    workflow_instance_id: Optional[int],
    grace_seconds: int,
) -> list[dict[str, Any]]:
    import pyodbc

    conn = pyodbc.connect(config.connection_string, autocommit=True, timeout=60)
    try:
        cur = conn.cursor()
        cur.execute(
            f"EXEC {config.schema_dot}sp_reclaim_expired_leases "
            "@workflow_instance_id=?, @grace_seconds=?, @quiet=0",
            (workflow_instance_id, grace_seconds),
        )
        if cur.description is None:
            return []
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _reclaim_postgres(
    config: Any,
    workflow_instance_id: Optional[int],
    grace_seconds: int,
) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(config.connection_string, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {config.schema_dot}sp_reclaim_expired_leases(%s, %s, false)",
                (workflow_instance_id, grace_seconds),
            )
            return [dict(r) for r in cur.fetchall()]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reset RUNNING workflow nodes with expired or missing task leases "
            "back to READY so workers can claim them again "
            "(Azure SQL and PostgreSQL)."
        )
    )
    parser.add_argument(
        "--instance-id",
        type=int,
        default=None,
        help="Limit reclaim to one workflow_instance_id (default: all instances)",
    )
    parser.add_argument(
        "--grace-seconds",
        type=int,
        default=60,
        help="Extra seconds after lease_expires before reclaim (default: 60)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print reclaimed rows as JSON",
    )
    args = parser.parse_args(argv)

    rows = reclaim_expired_leases(
        workflow_instance_id=args.instance_id,
        grace_seconds=args.grace_seconds,
    )
    if args.json:
        print(json.dumps(rows, default=str))
    else:
        if not rows:
            print("reclaim_expired_leases: 0 nodes reclaimed")
        else:
            print(f"reclaim_expired_leases: {len(rows)} node(s) reclaimed")
            for r in rows:
                print(
                    f"  ne={r.get('node_execution_id')} "
                    f"instance={r.get('workflow_instance_id')} "
                    f"prev_worker={r.get('previous_worker_id')}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
