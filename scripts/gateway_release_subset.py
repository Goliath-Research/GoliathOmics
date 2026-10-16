#!/usr/bin/env python3
"""Build the gateway-only payload (gateway-dist) from a GoliathOmics release.

Usage: gateway_release_subset.py SRC_RELEASE DEST_DIR

The gateway host is only the REST API gateway (workers -> DB). It never
receives the release bundle (worker wheels, CUDA/RAPIDS, methyl-extractor,
runtime-bundle, manifest). DEST_DIR gets:
  - wheels/: the wheel(s) declaring the methyl-gateway and
    methyl-reclaim-leases console scripts, plus the wheels/ entries in their
    Requires-Dist closure (anything else comes from the package index)
  - requirements-pipeline.txt: only the lines of the release's
    requirements-pipeline.txt that name a gateway dependency (the file
    install_gateway_venv.sh reads for pins)
Prints the file count and size. Exit 1 when no wheel provides methyl-gateway.
"""
from __future__ import annotations

import re
import shutil
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

GATEWAY_SCRIPTS = {"methyl-gateway", "methyl-reclaim-leases"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip("-").lower()


def dist_file(zf: zipfile.ZipFile, filename: str) -> str:
    for name in zf.namelist():
        if ".dist-info/" in name and name.endswith("/" + filename) and name.count("/") == 1:
            return zf.read(name).decode("utf-8", "replace")
    return ""


def console_scripts(text: str) -> set[str]:
    found, section = set(), False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower() == "[console_scripts]"
        elif section and "=" in line:
            found.add(line.split("=", 1)[0].strip())
    return found


def inspect(path: Path):
    with zipfile.ZipFile(path) as zf:
        meta = Parser().parsestr(dist_file(zf, "METADATA"))
        scripts = console_scripts(dist_file(zf, "entry_points.txt"))
    requires = set()
    for value in meta.get_all("Requires-Dist") or []:
        head, _, marker = value.partition(";")
        if re.search(r"extra\s*==", marker):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", head.strip())
        if m:
            requires.add(norm(m.group(1)))
    return norm(meta.get("Name") or path.name.split("-")[0]), scripts, requires


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    src, dest = Path(sys.argv[1]), Path(sys.argv[2])
    info = {}
    for whl in sorted((src / "wheels").glob("*.whl")):
        try:
            info[whl] = inspect(whl)
        except (zipfile.BadZipFile, OSError):
            print(f"[WARN] skipping unreadable wheel {whl.name}", file=sys.stderr)
    roots = [w for w, (_, scripts, _) in info.items() if scripts & GATEWAY_SCRIPTS]
    if not any("methyl-gateway" in info[w][1] for w in roots):
        print(f"[ERROR] no wheel under {src}/wheels provides the methyl-gateway console script "
              "(entry_points.txt [console_scripts])", file=sys.stderr)
        return 1
    by_name: dict[str, list[Path]] = {}
    for whl, (name, _, _) in info.items():
        by_name.setdefault(name, []).append(whl)
    chosen, todo = set(), list(roots)
    while todo:
        whl = todo.pop()
        if whl in chosen:
            continue
        chosen.add(whl)
        for dep in info[whl][2]:
            todo.extend(by_name.get(dep, []))
    (dest / "wheels").mkdir(parents=True, exist_ok=True)
    total = count = 0
    for whl in sorted(chosen):
        shutil.copy2(whl, dest / "wheels" / whl.name)
        total += whl.stat().st_size
        count += 1
    needed = set()
    for whl in chosen:
        needed |= info[whl][2]
    lines = []
    for cand in (src / "requirements-pipeline.txt", src / "runtime-bundle" / "requirements-pipeline.txt"):
        if cand.is_file():
            for raw in cand.read_text(encoding="utf-8").splitlines():
                body = raw.split("#", 1)[0].strip()
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", body)
                if m and not body.startswith("-") and norm(m.group(1)) in needed:
                    lines.append(body)
            break
    if lines:
        req = dest / "requirements-pipeline.txt"
        req.write_text("# gateway dependencies only (filtered from the release)\n" + "\n".join(lines) + "\n",
                       encoding="utf-8")
        total += req.stat().st_size
        count += 1
    print(f"gateway-dist: {count} files, {total / 1048576:.1f} MiB "
          f"({len(chosen)} of {len(info)} wheels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
