#!/usr/bin/env python3
"""Normalize first-party wheel metadata so offline installs never see build-host or VCS pins.

Usage: fix_wheel_path_deps.py WHEELS_DIR

Poetry path dependencies ({path = "../methylutils", develop = true}) are
written into the built wheel as
  Requires-Dist: methylutils @ file:///home/runner/.../packages/methylutils
Stale source pins can also appear as direct VCS references, e.g.
  Requires-Dist: methyl-gateway @ git+https://github.com/...
pip-compile then locks those URLs; pip with --require-hashes cannot hash VCS
refs. Each such requirement becomes
"name==<version of the matching wheel in WHEELS_DIR>" when a sibling wheel
exists (or bare "name" when none matches). METADATA and RECORD are rewritten in
place.

When the distribution was renamed methyl-gateway -> goliath-gateway, a
Requires-Dist on methyl-gateway may resolve to the goliath-gateway wheel
version in WHEELS_DIR; the rewritten line uses the goliath-gateway name.
"""
from __future__ import annotations

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

# Requires-Dist: name [extras]? @ file:...
REQ_FILE = re.compile(
    r"^Requires-Dist:\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*@\s*file:\S+?\s*(;.*)?$"
)
# Any other direct URL (git+https, https archive, etc.); not file:
REQ_VCS_URL = re.compile(
    r"^Requires-Dist:\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*@\s*(?!file:)\S+\s*(;.*)?$"
)


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build_version_index(wheels: Path) -> dict[str, str]:
    """Map normalized distribution name -> version string from wheel filenames."""
    versions: dict[str, str] = {}
    for whl in wheels.glob("*.whl"):
        parts = whl.name[:-4].split("-")
        if len(parts) >= 5:
            versions[norm(parts[0])] = parts[1]
    return versions


def resolve_pin(raw_name: str, versions: dict[str, str]) -> tuple[str, str | None]:
    """Return (dependency name for Requires-Dist, version or None).

    Prefer an exact wheel name match; map legacy methyl-gateway to
    goliath-gateway when only that wheel exists.
    """
    key = norm(raw_name)
    if key in versions:
        return raw_name, versions[key]
    gw, mg = norm("goliath-gateway"), norm("methyl-gateway")
    if key == mg and gw in versions:
        return "goliath-gateway", versions[gw]
    if key == gw and mg in versions:
        return "goliath-gateway", versions[mg]
    return raw_name, None


def rewrite_requires_line(line: str, versions: dict[str, str]) -> tuple[str, bool]:
    """If line is a path or URL Requires-Dist, return (new_line, True). Otherwise (line, False)."""
    for pat in (REQ_FILE, REQ_VCS_URL):
        m = pat.match(line)
        if not m:
            continue
        raw_name, extras, marker = m.group(1), m.group(2) or "", m.group(3) or ""
        name, ver = resolve_pin(raw_name, versions)
        req = f"{name}{extras}" + (f"=={ver}" if ver else "")
        new_line = f"Requires-Dist: {req}" + (f" {marker.strip()}" if marker else "")
        return new_line, True
    return line, False


def main() -> int:
    wheels = Path(sys.argv[1])
    versions = build_version_index(wheels)
    changed = 0
    for whl in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(whl) as zf:
            names = zf.namelist()
            meta_name = next((n for n in names if n.count("/") == 1 and n.endswith(".dist-info/METADATA")), None)
            if not meta_name:
                continue
            meta = zf.read(meta_name).decode("utf-8")
            new_lines: list[str] = []
            hit = False
            file_hits = 0
            vcs_hits = 0
            for line in meta.split("\n"):
                new_line, did = rewrite_requires_line(line, versions)
                if did:
                    hit = True
                    if REQ_FILE.match(line):
                        file_hits += 1
                    elif REQ_VCS_URL.match(line):
                        vcs_hits += 1
                new_lines.append(new_line)
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
        parts = [f"{whl.name}: normalized Requires-Dist"]
        if file_hits:
            parts.append(f"file:// ({file_hits})")
        if vcs_hits:
            parts.append(f"URL/VCS ({vcs_hits})")
        print(f"[INFO] {'; '.join(parts)} -> name==version")
    print(f"[INFO] fix_wheel_path_deps: {changed} wheel(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
