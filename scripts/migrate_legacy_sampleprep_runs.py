#!/usr/bin/env python3
"""Copy completed SamplePrep runs from the legacy GoliathOmics database into a target database.

Dry run by default: the whole copy runs inside one target transaction and is
rolled back. Pass --apply to commit. Reruns skip instances already copied
(instance extension ``legacy.migration.source``).

Connection strings come from environment variables (never pass secrets on the
command line):

  LEGACY_SOURCE_DSN   legacy GoliathOmics database (opened READ ONLY)
  MIGRATION_TARGET_DSN target database (for example GoliathOmics_v2)

Use --source-dsn-env / --target-dsn-env to read other variable names.
See docs/operators/legacy-run-migration.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "workflow_engine"))

from ops.legacy_run_migration import (  # noqa: E402
    MigrationError,
    copy_runs,
    database_identity,
    load_source_runs,
)

DEFAULT_INSTANCE_IDS = "14,15,16,17,18,19"


def _parse_ids(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance-ids", default=DEFAULT_INSTANCE_IDS, help="source instance ids, e.g. 14-19")
    ap.add_argument("--workflow-name", default="SamplePrepPipeline")
    ap.add_argument("--source-label", default="GoliathOmics-legacy",
                    help="stable label stored in provenance; reruns with the same label are no-ops")
    ap.add_argument("--source-dsn-env", default="LEGACY_SOURCE_DSN")
    ap.add_argument("--target-dsn-env", default="MIGRATION_TARGET_DSN")
    ap.add_argument("--apply", action="store_true", help="commit (default: dry run, rolled back)")
    args = ap.parse_args(argv)

    src_dsn = os.environ.get(args.source_dsn_env)
    tgt_dsn = os.environ.get(args.target_dsn_env)
    if not src_dsn or not tgt_dsn:
        print(f"Set {args.source_dsn_env} and {args.target_dsn_env}", file=sys.stderr)
        return 2

    import psycopg
    from psycopg.rows import dict_row

    ids = _parse_ids(args.instance_ids)
    with psycopg.connect(src_dsn, row_factory=dict_row) as src:
        src.read_only = True
        with src.cursor() as cur:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            source_db, source_sysid = database_identity(cur)
            source = load_source_runs(cur, ids, workflow_name=args.workflow_name)
        src.rollback()

    with psycopg.connect(tgt_dsn, row_factory=dict_row) as tgt:
        with tgt.cursor() as cur:
            target_db, target_sysid = database_identity(cur)
            if (source_db, source_sysid) == (target_db, target_sysid):
                print("source and target are the same database; refusing", file=sys.stderr)
                return 2
            try:
                summary = copy_runs(cur, source, source_label=args.source_label, source_database=source_db)
            except MigrationError:
                tgt.rollback()
                raise
        if args.apply:
            tgt.commit()
        else:
            tgt.rollback()

    report = {
        "mode": "apply" if args.apply else "dry-run (rolled back)",
        "source_database": source_db,
        "target_database": target_db,
        "source_counts": source.counts(),
        **summary,
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
