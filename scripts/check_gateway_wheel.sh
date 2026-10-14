#!/bin/bash
# Fail unless a wheel declares the methyl-gateway console script.
#
# Filename is not evidence. Plugin wheels (methyl_worker, methylutils, …)
# live under wheels/ and do not provide the gateway entry point. The core
# package wheel does, in *.dist-info/entry_points.txt under [console_scripts].

set -euo pipefail

WHEELS="${1:-}"
if [[ -z "$WHEELS" || ! -d "$WHEELS" ]]; then
  echo "[ERROR] usage: $0 WHEELS_DIR" >&2
  exit 1
fi

python3 - "$WHEELS" <<'PY'
import sys
import zipfile
from pathlib import Path

wheels_dir = Path(sys.argv[1])
script_name = "methyl-gateway"

def console_scripts(text):
    found = []
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line.lower() == "[console_scripts]"
            continue
        if in_section and "=" in line:
            found.append(line.split("=", 1)[0].strip())
    return found

matches = []
seen = 0
for path in sorted(wheels_dir.glob("*.whl")):
    seen += 1
    try:
        with zipfile.ZipFile(path) as archive:
            entry = ""
            for name in archive.namelist():
                if ".dist-info/" in name and name.endswith("/entry_points.txt"):
                    entry = archive.read(name).decode("utf-8", "replace")
                    break
    except zipfile.BadZipFile:
        print("[WARN] skipping invalid wheel %s" % path, file=sys.stderr)
        continue
    scripts = console_scripts(entry)
    if script_name in scripts:
        matches.append(path.name)

if matches:
    print("[INFO] methyl-gateway console script provided by: %s" % ", ".join(matches))
    sys.exit(0)

print(
    "[ERROR] no wheel in %s provides the methyl-gateway console script. "
    "Checked entry_points.txt [console_scripts] in %d wheel(s). "
    "The core package wheel must be built into wheels/; plugin wheels alone are not enough."
    % (wheels_dir, seen),
    file=sys.stderr,
)
sys.exit(1)
PY
