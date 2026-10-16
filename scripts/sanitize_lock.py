#!/usr/bin/env python3
"""Make a requirements lock independent of the machine that built it.

Usage: sanitize_lock.py LOCK [--wheels-dir DIR]

pip-compile run with --find-links on locally built wheels can write
first-party pins as direct references to the build host
("methyl-worker @ file:///home/runner/work/_temp/release/wheels/x.whl") and
mention that path in "# via" / "# from" comments. This rewrites, in place:
  - "name @ file://.../name-ver-...whl" -> "name==ver" (hash lines kept; they
    hash the same wheel file that ships in wheels/)
  - "name @ file:///<checkout>/packages/<pkg>" (a source directory, from a
    poetry path dependency) -> "name==ver" of the matching wheel in
    --wheels-dir; no matching wheel is an error
  - drops -f/--find-links/-i/--index-url/--extra-index-url/--trusted-host lines
  - replaces build-host paths inside comments with "wheels/"
Exits 1 if a requirement line still references file:// or the wheels dir.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

OPTION = re.compile(r"^\s*(-f|--find-links|-i|--index-url|--extra-index-url|--trusted-host)(\s|=|$)")
DIR_REF = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?\s*@\s*(?P<url>file://\S+?)(?P<rest>\s*(;[^\\]*)?\s*\\?\s*)$")
DIRECT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?\s*@\s*(?P<url>file://\S+?\.whl)(?P<rest>\s*(;[^\\]*)?\s*\\?\s*)$")
FILE_URL = re.compile(r"file://\S+")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_versions(wheels_dir: str | None) -> dict[str, str]:
    found: dict[str, str] = {}
    if wheels_dir and Path(wheels_dir).is_dir():
        for whl in Path(wheels_dir).glob("*.whl"):
            parts = whl.name[:-4].split("-")
            if len(parts) >= 5:
                found[norm(parts[0])] = parts[1]
    return found


def sanitize(text: str, wheels_dir: str | None) -> tuple[str, list[str]]:
    out, bad = [], []
    versions = wheel_versions(wheels_dir)
    for raw in text.splitlines():
        line = raw
        if OPTION.match(line):
            continue
        stripped = line.lstrip()
        if stripped.startswith("#"):
            line = FILE_URL.sub("wheels/", line)
            if wheels_dir:
                line = line.replace(wheels_dir.rstrip("/"), "wheels")
            out.append(line)
            continue
        m = DIRECT.match(stripped)
        if m:
            fname = Path(unquote(urlparse(m.group("url")).path)).name
            parts = fname[:-4].split("-")
            if len(parts) < 5:
                bad.append(raw)
                out.append(line)
                continue
            indent = line[: len(line) - len(stripped)]
            line = f"{indent}{m.group('name')}=={parts[1]}{m.group('rest').rstrip()}"
            if line.endswith("\\") and not line.endswith(" \\"):
                line = line[:-1].rstrip() + " \\"
        elif DIR_REF.match(stripped):
            d = DIR_REF.match(stripped)
            ver = versions.get(norm(d.group("name")))
            if ver:
                indent = line[: len(line) - len(stripped)]
                line = f"{indent}{d.group('name')}=={ver}{d.group('rest').rstrip()}"
                if line.endswith("\\") and not line.endswith(" \\"):
                    line = line[:-1].rstrip() + " \\"
            else:
                print(f"NO BUILT WHEEL for source reference: {stripped}", file=sys.stderr)
        code = line.split(" #", 1)[0]
        if "file://" in code or (wheels_dir and wheels_dir.rstrip("/") in code):
            bad.append(raw)
        out.append(line)
    return "\n".join(out) + "\n", bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lock")
    ap.add_argument("--wheels-dir")
    a = ap.parse_args()
    lock = Path(a.lock)
    new, bad = sanitize(lock.read_text(encoding="utf-8"), a.wheels_dir)
    lock.write_text(new, encoding="utf-8")
    for b in bad:
        print(f"BUILD-HOST PATH: {b.strip()}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
