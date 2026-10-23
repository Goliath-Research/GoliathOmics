#!/usr/bin/env python3
"""Bundle GoliathApp's PostgreSQL scripts into a GoliathOmics release.

Usage:
  bundle_goliathapp_sql.py --sql-dir DIR --out DIR --commit SHA [--repository URL]
  bundle_goliathapp_sql.py --sql-dir DIR --list

DIR is GoliathApp workflow_engine/sql_pg. The apply order is not copied by
hand: it is read from the SCRIPTS=( ... ) array in DIR/apply_schema.sh, the
script GoliathApp itself uses to apply PostgreSQL schema. A file added there is
bundled (and applied) by the next release without a change in this repo.

--out receives:
  sql_pg/<name>.sql   the listed scripts, byte for byte
  manifest.sha256     "<sha256>  sql_pg/<name>.sql" per script, in apply
                      order (sha256sum -c format)
  source.json         GoliathApp repository, commit, order source, count

scripts/apply_goliathapp_schema.sh applies the bundle on deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ORDER_FILE = "apply_schema.sh"
NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.sql")
COMMIT_RE = re.compile(r"[A-Za-z0-9._-]+")
DEFAULT_REPOSITORY = "https://github.com/Goliath-Research/GoliathApp"


class BundleError(Exception):
    """The GoliathApp checkout cannot produce a complete, ordered bundle."""


def parse_order(order_file: Path) -> list[str]:
    """Return the SCRIPTS array of apply_schema.sh, in order."""
    try:
        text = order_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleError(f"cannot read {order_file}: {exc}") from exc
    names: list[str] = []
    in_array = False
    found = False
    for raw in text.splitlines():
        line = raw.strip()
        if not in_array:
            if re.fullmatch(r"SCRIPTS=\(\s*", line):
                in_array = True
                found = True
            continue
        if line.startswith(")"):
            in_array = False
            break
        line = line.split("#", 1)[0].strip()
        for token in line.split():
            token = token.strip("'\"")
            if not NAME_RE.fullmatch(token):
                raise BundleError(f"{order_file}: unexpected SCRIPTS entry {token!r}")
            names.append(token)
    if not found:
        raise BundleError(f"{order_file}: no SCRIPTS=( array")
    if in_array:
        raise BundleError(f"{order_file}: SCRIPTS=( array is not closed")
    if not names:
        raise BundleError(f"{order_file}: SCRIPTS array is empty")
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise BundleError(f"{order_file}: {name} is listed twice")
        seen.add(name)
    return names


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bundle(sql_dir: Path, out: Path, commit: str, repository: str) -> int:
    if not COMMIT_RE.fullmatch(commit):
        raise BundleError(f"GoliathApp commit is not safe to record: {commit!r}")
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
        # Content only: the shared /work volume rejects chmod and ownership.
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
    parser.add_argument("--sql-dir", required=True, type=Path, help="GoliathApp workflow_engine/sql_pg")
    parser.add_argument("--out", type=Path, help="bundle directory to (re)create")
    parser.add_argument("--commit", default="", help="GoliathApp commit SHA the scripts come from")
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
    print(f"[INFO] bundled {count} GoliathApp sql_pg scripts ({args.commit}) into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
