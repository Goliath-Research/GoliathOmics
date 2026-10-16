#!/usr/bin/env python3
"""Replace build-host path dependencies in first-party wheel metadata.

Usage: fix_wheel_path_deps.py WHEELS_DIR

Poetry path dependencies ({path = "../methylutils", develop = true}) are
written into the built wheel as
  Requires-Dist: methylutils @ file:///home/runner/.../packages/methylutils
pip-compile then pins those source directories in the lock, and pip on a pod
would try to fetch a runner path. Each such requirement becomes
"name==<version of the matching wheel in WHEELS_DIR>" (or bare "name" when no
wheel matches). METADATA and RECORD are rewritten in place.
"""
from __future__ import annotations

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

REQ = re.compile(r"^Requires-Dist:\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*@\s*file:\S+?\s*(;.*)?$")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def main() -> int:
    wheels = Path(sys.argv[1])
    versions = {}
    for whl in wheels.glob("*.whl"):
        parts = whl.name[:-4].split("-")
        if len(parts) >= 5:
            versions[norm(parts[0])] = parts[1]
    changed = 0
    for whl in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            meta_name = next((n for n in names if n.count("/") == 1 and n.endswith(".dist-info/METADATA")), None)
            if not meta_name:
                continue
            meta = zf.read(meta_name).decode("utf-8")
            new_lines, hit = [], False
            for line in meta.split("\n"):
                m = REQ.match(line)
                if m:
                    name, extras, marker = m.group(1), m.group(2) or "", m.group(3) or ""
                    ver = versions.get(norm(name))
                    req = f"{name}{extras}" + (f"=={ver}" if ver else "")
                    line = f"Requires-Dist: {req}" + (f" {marker.strip()}" if marker else "")
                    hit = True
                new_lines.append(line)
            if not hit:
                continue
            new_meta = "\n".join(new_lines).encode("utf-8")
            entries = {n: zf.read(n) for n in names}
            infos = {i.filename: i for i in zf.infolist()}
        entries[meta_name] = new_meta
        rec_name = meta_name[: -len("METADATA")] + "RECORD"
        if rec_name in entries:
            rows = []
            for row in entries[rec_name].decode("utf-8").splitlines():
                if row.split(",", 1)[0] == meta_name:
                    row = f"{meta_name},{record_hash(new_meta)},{len(new_meta)}"
                rows.append(row)
            entries[rec_name] = ("\n".join(rows) + "\n").encode("utf-8")
        tmp = whl.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for n in names:
                out.writestr(infos[n], entries[n])
        tmp.replace(whl)
        changed += 1
        print(f"[INFO] {whl.name}: replaced file:// path dependencies with name==version")
    print(f"[INFO] fix_wheel_path_deps: {changed} wheel(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
