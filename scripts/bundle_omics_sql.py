#!/usr/bin/env python3
"""Bundle GoliathOmics domain PostgreSQL scripts into a release.

Usage:
  bundle_omics_sql.py --sql-dir DIR --out DIR --commit SHA [--repository URL]
  bundle_omics_sql.py --sql-dir DIR --list

DIR is GoliathOmics workflow_engine/sql_pg. Apply order is read from
DIR/apply_schema.sh SCRIPTS=( ... ).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from bundle_goliathapp_sql import BundleError, parse_order, sha256_file

ORDER_FILE = "apply_schema.sh"
COMMIT_RE = re.compile(r"[A-Za-z0-9._-]+")
DEFAULT_REPOSITORY = "https://github.com/Goliath-Research/GoliathOmics"


def write_bundle(sql_dir: Path, out: Path, commit: str, repository: str) -> int:
    if not COMMIT_RE.fullmatch(commit):
        raise BundleError(f"GoliathOmics commit is not safe to record: {commit!r}")
    names = parse_order(sql_dir / ORDER_FILE)
    sources: list[Path] = []
    for name in names:
        path = sql_dir / name
        if path.is_symlink() or not path.is_file():
            raise BundleError(f"{ORDER_FILE} lists {name} but {path} is not a regular file")
        sources.append(path)
    if out.exists():
        shutil.rmtree(out)
    (out / "sql_pg").mkdir(parents=True)
    lines: list[str] = []
    for name, path in zip(names, sources):
        dest = out / "sql_pg" / name
        dest.write_bytes(path.read_bytes())
        lines.append(f"{sha256_file(dest)}  sql_pg/{name}")
    (out / "manifest.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    source = {
        "repository": repository,
        "commit": commit,
        "order_source": f"workflow_engine/sql_pg/{ORDER_FILE}",
        "scripts": len(names),
    }
    (out / "source.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    return len(names)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sql-dir", required=True, type=Path, help="GoliathOmics workflow_engine/sql_pg")
    parser.add_argument("--out", type=Path, help="bundle directory to (re)create")
    parser.add_argument("--commit", default="", help="GoliathOmics commit SHA the scripts come from")
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--list", action="store_true", help="print the apply order and exit")
    args = parser.parse_args(argv)
    sql_dir: Path = args.sql_dir
    try:
        if args.list:
            for name in parse_order(sql_dir / ORDER_FILE):
                print(name)
            return 0
        if args.out is None or not args.commit:
            parser.error("--out and --commit are required unless --list is given")
        count = write_bundle(sql_dir, args.out, args.commit, args.repository)
    except BundleError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[INFO] bundled {count} GoliathOmics sql_pg scripts ({args.commit}) into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
