#!/usr/bin/env bash
# The release build must put a wheel in wheels/ whose entry_points.txt
# declares the methyl-gateway console script. The wheel version comes from
# --version, not from a fixed release number.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/scripts/build_gateway_wheel.sh"
CHECK="$ROOT/scripts/check_gateway_wheel.sh"
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -f "$BUILD" && -f "$CHECK" ]] || fail "missing gateway wheel scripts"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

src="$work/source"
mkdir -p "$src/rest"
cat >"$src/rest/__init__.py" <<'EOF'
EOF
cat >"$src/rest/gateway.py" <<'EOF'
def main():
    return 0
EOF
cat >"$src/pyproject.toml" <<'EOF'
[project]
name = "methyl-gateway"
version = "1.0.0"
description = "fixture core package"
requires-python = ">=3.10"
dependencies = []

[project.scripts]
methyl-gateway = "rest.gateway:main"
methyl-reclaim-leases = "ops.reclaim_expired_leases:main"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["rest*", "ops*", "admin*"]
EOF

wheels="$work/wheels"
sha_file="$work/sha"
bash "$BUILD" --version 9.8.7 --wheels "$wheels" --source "$src" --sha-file "$sha_file"
[[ "$(tr -d '[:space:]' <"$sha_file")" == "local-source" ]] || fail "fixture source should record local-source"

wheel="$(echo "$wheels"/methyl_gateway-9.8.7-*.whl)"
[[ -f "$wheel" ]] || fail "expected methyl_gateway-9.8.7 wheel, found: $(ls -A "$wheels")"
if ls "$wheels"/methyl_gateway-1.0.0-*.whl >/dev/null 2>&1; then
  fail "wheel kept the pyproject version 1.0.0 instead of --version"
fi

python3 - "$wheel" <<'PY'
import sys
import zipfile
from pathlib import Path

path = Path(sys.argv[1])
with zipfile.ZipFile(path) as archive:
    names = archive.namelist()
    entry = ""
    metadata = ""
    for name in names:
        if name.endswith("/entry_points.txt") and ".dist-info/" in name:
            entry = archive.read(name).decode()
        if name.endswith("/METADATA") and ".dist-info/" in name:
            metadata = archive.read(name).decode()
if "Version: 9.8.7" not in metadata:
    raise SystemExit("METADATA version is not the release version")
if "methyl-gateway = rest.gateway:main" not in entry:
    raise SystemExit("entry_points.txt missing methyl-gateway")
if "methyl-reclaim-leases = ops.reclaim_expired_leases:main" not in entry:
    raise SystemExit("entry_points.txt missing methyl-reclaim-leases")
if not any(name.startswith("ops/reclaim_expired_leases.py") for name in names):
    raise SystemExit("reclaim module was not overlaid into the wheel")
PY

bash "$CHECK" "$wheels" >/dev/null

empty="$work/plugins-only"
mkdir -p "$empty"
python3 - "$empty" <<'PY'
import sys
import zipfile
from pathlib import Path

path = Path(sys.argv[1]) / "methyl_worker-1.0.0-py3-none-any.whl"
meta = "Metadata-Version: 2.1\nName: methyl-worker\nVersion: 1.0.0\n\n"
entry = "[console_scripts]\nmethyl-worker = methyl_worker:main\n"
with zipfile.ZipFile(path, "w") as archive:
    archive.writestr("methyl_worker-1.0.0.dist-info/METADATA", meta)
    archive.writestr("methyl_worker-1.0.0.dist-info/entry_points.txt", entry)
PY
set +e
bash "$CHECK" "$empty" >"$work/check.out" 2>"$work/check.err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "plugin wheels must not satisfy the methyl-gateway check"
grep -q 'methyl-gateway console script' "$work/check.err" || fail "check error should name the console script"

if grep -R -n '2026.10.0' "$BUILD" "$CHECK" "$ROOT/scripts/build_release.sh" "$ROOT/.github/workflows/release.yml" >/dev/null 2>&1; then
  fail "release packaging hardcodes 2026.10.0"
fi

echo "test_gateway_wheel_release ok"
