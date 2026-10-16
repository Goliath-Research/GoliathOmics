#!/usr/bin/env python3
"""Check that every name==version pin in a requirements lock has a wheel.

Usage: verify_locked_wheels.py LOCK WHEELS_DIR [--arch x86_64 --arch aarch64]
       [--python-version 3.12] [--list-missing]

A wheel counts for an arch when its platform tag is "any" or a linux /
manylinux / musllinux tag for that machine, and its python tag is py3,
py3<minor>, cp3<minor>, or an abi3 cp3x build with x <= minor. Pins whose
environment marker is false for (linux, arch, python) are skipped. Exit 1
and print one line per missing (name, version, arch) when anything is missing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from packaging.markers import Marker
    from packaging.version import InvalidVersion, Version
except Exception:  # packaging is optional
    Marker = None
    Version = None

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*([^\s;\\]+)\s*(?:;\s*([^\\#]+?))?\s*(?:\\)?\s*(?:#.*)?$")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def norm_ver(v: str) -> str:
    if Version is not None:
        try:
            return str(Version(v))
        except InvalidVersion:
            pass
    return v


def read_pins(lock: Path):
    pins = []
    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "--")):
            continue
        m = PIN.match(line)
        if m:
            pins.append((norm(m.group(1)), norm_ver(m.group(2)), (m.group(3) or "").strip()))
    return pins


def marker_ok(marker: str, arch: str, pyver: str) -> bool:
    if not marker or Marker is None:
        return True
    env = {
        "python_version": pyver,
        "python_full_version": pyver + ".0",
        "platform_machine": arch,
        "sys_platform": "linux",
        "platform_system": "Linux",
        "os_name": "posix",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "extra": "",
    }
    try:
        return Marker(marker).evaluate(env)
    except Exception:
        return True


def wheel_tags(fname: str):
    parts = fname[:-4].split("-")
    if len(parts) not in (5, 6):
        return None
    name, ver = parts[0], parts[1]
    py, abi, plat = parts[-3], parts[-2], parts[-1]
    return norm(name), norm_ver(ver), py.split("."), abi.split("."), plat.split(".")


def py_ok(pys, abis, minor: int) -> bool:
    for py in pys:
        if py in ("py3", f"py3{minor}", f"cp3{minor}"):
            return True
        m = re.fullmatch(r"cp3(\d+)", py)
        if m and "abi3" in abis and int(m.group(1)) <= minor:
            return True
    return False


def plat_ok(plats, arch: str) -> bool:
    for p in plats:
        if p == "any":
            return True
        # Worker images are glibc; musllinux wheels do not count.
        if p.startswith(("manylinux", "linux")) and p.endswith("_" + arch):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("lock")
    ap.add_argument("wheels")
    ap.add_argument("--arch", action="append")
    ap.add_argument("--python-version", default="3.12")
    ap.add_argument("--list-missing", action="store_true", help="print name==version only, one per line")
    a = ap.parse_args()
    arches = a.arch or ["x86_64", "aarch64"]
    minor = int(a.python_version.split(".")[1])
    have = {}
    for whl in Path(a.wheels).glob("*.whl"):
        t = wheel_tags(whl.name)
        if t:
            have.setdefault((t[0], t[1]), []).append(t[2:])
    missing = []
    pins = read_pins(Path(a.lock))
    for name, ver, marker in pins:
        for arch in arches:
            if not marker_ok(marker, arch, a.python_version):
                continue
            if not any(py_ok(p, ab, minor) and plat_ok(pl, arch) for p, ab, pl in have.get((name, ver), [])):
                missing.append((name, ver, arch))
    if a.list_missing:
        for pin in sorted({f"{n}=={v}" for n, v, _ in missing}):
            print(pin)
        return 1 if missing else 0
    for n, v, arch in missing:
        print(f"MISSING {n}=={v} for linux {arch} cp3{minor}", file=sys.stderr)
    print(f"verify_locked_wheels: {len(pins)} pins, {len(arches)} arches, {len(missing)} missing", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
