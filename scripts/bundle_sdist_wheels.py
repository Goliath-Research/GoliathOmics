#!/usr/bin/env python3
"""Build wheels at release time for locked pins that only ship an sdist.

Usage: bundle_sdist_wheels.py LOCK WHEELS_DIR [--arch x86_64 ...]
       [--python-version 3.12] [--python python3] [-- PIP_INDEX_ARGS...]

For every pin verify_locked_wheels.py reports missing, this downloads the
pin's sdist (checked against the lock's --hash lines when the lock has
hashes), runs `pip wheel --no-deps` on it, and keeps the wheel when it is
pure Python (`py3-none-any`, valid for every arch) or when it was built for
this host's arch and that arch is requested. It then appends the built
wheel's sha256 to the pin's --hash lines in LOCK, so `pip install
--require-hashes --no-index --find-links WHEELS_DIR` on a pod accepts it.

Example: python-louvain==0.16 is published only as an sdist.
Pins that still have no wheel afterwards are listed on stderr; the strict
verify_locked_wheels.py run that follows fails the release for them.
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_locked_wheels as vlw  # noqa: E402


def missing_pins(lock: Path, wheels: Path, arches, pyver: str):
    have = {}
    minor = int(pyver.split(".")[1])
    for whl in wheels.glob("*.whl"):
        t = vlw.wheel_tags(whl.name)
        if t:
            have.setdefault((t[0], t[1]), []).append(t[2:])
    out = {}
    for name, ver, marker in vlw.read_pins(lock):
        for arch in arches:
            if not vlw.marker_ok(marker, arch, pyver):
                continue
            if not any(vlw.py_ok(p, ab, minor) and vlw.plat_ok(pl, arch) for p, ab, pl in have.get((name, ver), [])):
                out.setdefault(f"{name}=={ver}", []).append(arch)
    return out


def add_hash(lock: Path, pin: str, digest: str) -> bool:
    """Append --hash=sha256:DIGEST to PIN's block when the lock uses hashes."""
    lines = lock.read_text(encoding="utf-8").splitlines()
    if not any("--hash=" in l for l in lines):
        return False
    want_name, _, want_ver = pin.partition("==")
    out, i, done = [], 0, False
    while i < len(lines):
        line = lines[i]
        m = vlw.PIN.match(line.strip())
        if not done and m and vlw.norm(m.group(1)) == vlw.norm(want_name) and vlw.norm_ver(m.group(2)) == vlw.norm_ver(want_ver):
            block = [line]
            while block[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                block.append(lines[i])
            if f"sha256:{digest}" not in "\n".join(block):
                indent = "    "
                hl = [l for l in block[1:] if "--hash=" in l]
                if hl:
                    indent = re.match(r"\s*", hl[-1]).group(0)
                if not block[-1].rstrip().endswith("\\"):
                    block[-1] = block[-1].rstrip() + " \\"
                block.append(f"{indent}--hash=sha256:{digest}")
            out.extend(block)
            done = True
        else:
            out.append(line)
        i += 1
    lock.write_text("\n".join(out) + "\n", encoding="utf-8")
    return done


def main() -> int:
    argv = sys.argv[1:]
    pip_extra = []
    if "--" in argv:
        k = argv.index("--")
        argv, pip_extra = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("lock")
    ap.add_argument("wheels")
    ap.add_argument("--arch", action="append")
    ap.add_argument("--python-version", default="3.12")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    lock, wheels = Path(a.lock), Path(a.wheels)
    arches = a.arch or ["x86_64"]
    host_arch = platform.machine()
    missing = missing_pins(lock, wheels, arches, a.python_version)
    if not missing:
        print("bundle_sdist_wheels: nothing missing", file=sys.stderr)
        return 0
    hashed = "--hash=" in lock.read_text(encoding="utf-8")
    still = []
    for pin, pin_arches in sorted(missing.items()):
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            req = tdp / "req.txt"
            req.write_text(vlw.pin_block(lock, pin), encoding="utf-8")
            dl = [a.python, "-m", "pip", "download", "-q", "--no-deps", "--no-binary", ":all:",
                  "--dest", str(tdp / "sdist"), *pip_extra, "-r", str(req)]
            if hashed:
                dl.append("--require-hashes")
            if subprocess.run(dl).returncode != 0:
                print(f"bundle_sdist_wheels: no sdist for {pin}", file=sys.stderr)
                still.append(pin)
                continue
            sdists = list((tdp / "sdist").iterdir())
            if len(sdists) != 1:
                still.append(pin)
                continue
            wb = [a.python, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(tdp / "whl"), *pip_extra, str(sdists[0])]
            if subprocess.run(wb).returncode != 0:
                print(f"bundle_sdist_wheels: pip wheel failed for {pin}", file=sys.stderr)
                still.append(pin)
                continue
            built = list((tdp / "whl").glob("*.whl"))
            if len(built) != 1:
                still.append(pin)
                continue
            whl = built[0]
            tags = vlw.wheel_tags(whl.name)
            pure = tags is not None and tags[4] == ["any"]
            if not pure and not (host_arch in pin_arches and set(pin_arches) == {host_arch}):
                print(f"bundle_sdist_wheels: {pin} builds a native wheel ({whl.name}) on {host_arch}; "
                      f"build it on {', '.join(a for a in pin_arches if a != host_arch)} too", file=sys.stderr)
                if host_arch not in pin_arches:
                    still.append(pin)
                    continue
            dest = wheels / whl.name
            dest.write_bytes(whl.read_bytes())
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            add_hash(lock, pin, digest)
            print(f"bundle_sdist_wheels: built {whl.name} from sdist ({pin}){' + lock hash' if hashed else ''}",
                  file=sys.stderr)
    for pin in still:
        print(f"bundle_sdist_wheels: still no wheel for {pin}", file=sys.stderr)
    return 1 if still else 0


if __name__ == "__main__":
    sys.exit(main())
