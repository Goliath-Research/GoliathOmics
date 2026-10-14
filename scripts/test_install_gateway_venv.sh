#!/usr/bin/env bash
# Installs methyl-gateway into venv-<arch> from a release wheel. No SSH.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/install_gateway_venv.sh"
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -x "$SCRIPT" || -f "$SCRIPT" ]] || fail "missing install_gateway_venv.sh"
bash "$SCRIPT" --help >/dev/null

assert_requires() {
  local spec="$1" version="$2" expect="$3"
  set +e
  bash "$SCRIPT" --check-requires "$spec" "$version" >/dev/null
  local status=$?
  set -e
  if [[ "$expect" == yes && "$status" -ne 0 ]]; then
    fail "Requires-Python '$spec' should accept $version"
  fi
  if [[ "$expect" == no && "$status" -eq 0 ]]; then
    fail "Requires-Python '$spec' should reject $version"
  fi
}

assert_requires '>=3.10,<3.15' 3.14.4 yes
assert_requires '>=3.10,<3.14' 3.14.4 no
assert_requires '>=3.10,<3.14' 3.13.1 yes
assert_requires '~=3.12.0' 3.12.3 yes
assert_requires '~=3.12.0' 3.13.0 no
assert_requires '!=3.14.*' 3.14.4 no
assert_requires '!=3.14.*' 3.13.1 yes
assert_requires '==3.12.*' 3.12.3 yes
assert_requires '>=3.10,<3.15' 3.12.3 yes

selected="$(bash "$SCRIPT" --select-python '>=3.10,<3.14')"
[[ "$selected" == "3.13" ]] || fail "expected uv to provision 3.13 for >=3.10,<3.14, got $selected"
selected="$(bash "$SCRIPT" --select-python '>=3.12,<3.13')"
[[ "$selected" == "3.12" ]] || fail "expected 3.12 for >=3.12,<3.13, got $selected"
selected="$(bash "$SCRIPT" --select-python '==3.11.9')"
[[ "$selected" == "3.11.9" ]] || fail "expected exact 3.11.9, got $selected"

err="$(mktemp)"
set +e
bash "$SCRIPT" --check-requires 'not a specifier' 3.14.4 >/dev/null 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "invalid Requires-Python should fail"
grep -q 'Requires-Python' "$err" || fail "invalid Requires-Python should name the field"
rm -f "$err"

build_wheel() {
  local dest="$1" name="$2" version="$3" requires="$4" scripts="$5" requires_dist="${6:-}"
  python3 - "$dest" "$name" "$version" "$requires" "$scripts" "$requires_dist" <<'PY'
import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

dest, name, version, requires, scripts, requires_dist = sys.argv[1:7]
norm = re.sub(r"[-_.]+", "_", name).strip("_").lower()
dist = "%s-%s" % (norm, version)
wheel_path = Path(dest) / ("%s-%s-py3-none-any.whl" % (norm, version))
wheel_path.parent.mkdir(parents=True, exist_ok=True)
module = b"def main():\n    print('gateway')\n\ndef reclaim():\n    print('reclaim')\n"
metadata = (
    "Metadata-Version: 2.1\n"
    "Name: %s\n"
    "Version: %s\n"
    "Summary: methyl gateway test wheel\n" % (name, version)
)
if requires:
    metadata += "Requires-Python: %s\n" % requires
for dep in [item.strip() for item in requires_dist.split(",") if item.strip()]:
    metadata += "Requires-Dist: %s\n" % dep
metadata += "\n"
wheel_meta = "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
files = {
    "methyl_gateway.py": module,
    "%s.dist-info/METADATA" % dist: metadata.encode(),
    "%s.dist-info/WHEEL" % dist: wheel_meta.encode(),
}
if scripts.strip():
    files["%s.dist-info/entry_points.txt" % dist] = ("[console_scripts]\n%s\n" % scripts.strip()).encode()

def record_hash(data):
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode().rstrip("=")

lines = []
for path, data in files.items():
    lines.append("%s,%s,%d" % (path, record_hash(data), len(data)))
record = "%s.dist-info/RECORD" % dist
lines.append("%s,," % record)
files[record] = ("\n".join(lines) + "\n").encode()
with zipfile.ZipFile(wheel_path, "w") as archive:
    for path, data in files.items():
        archive.writestr(path, data)
print(wheel_path)
PY
}

entry_scripts='methyl-gateway = methyl_gateway:main
methyl-reclaim-leases = methyl_gateway:reclaim'

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
release="$work/release"
root="$work/root"
mkdir -p "$release/wheels"
build_wheel "$release/wheels" methyl-pipeline 1.0.0 '>=3.10,<3.15' "$entry_scripts" >/dev/null
# A worker wheel without the gateway scripts must not be selected.
build_wheel "$release/wheels" methyl-worker 1.0.0 '>=3.10,<3.15' '' >/dev/null

out="$(mktemp)"
err="$(mktemp)"
bash "$SCRIPT" --root "$root" --arch amd64 --release "$release" >"$out" 2>"$err"
[[ -x "$root/venv-amd64/bin/methyl-gateway" ]] || fail "methyl-gateway entry point was not installed"
[[ -x "$root/venv-amd64/bin/methyl-reclaim-leases" ]] || fail "methyl-reclaim-leases entry point was not installed"
[[ "$("$root/venv-amd64/bin/methyl-gateway")" == "gateway" ]] || fail "methyl-gateway did not run"
[[ "$("$root/venv-amd64/bin/methyl-reclaim-leases")" == "reclaim" ]] || fail "methyl-reclaim-leases did not run"
grep -q 'methyl_pipeline-1.0.0' "$out" || fail "install should use the methyl-pipeline wheel"
if grep -q 'methyl_worker-1.0.0' "$out"; then
  fail "install selected the worker wheel"
fi
grep -q 'satisfies Requires-Python' "$out" || fail "install should record the Requires-Python check"

bash "$SCRIPT" --root "$root" --arch amd64 --release "$release" >"$out" 2>"$err"
grep -q 'reusing venv' "$out" || fail "second install should reuse the venv"
[[ -x "$root/venv-amd64/bin/methyl-gateway" ]] || fail "reused venv lost methyl-gateway"

# System python reports 3.14.4 and the wheel rejects 3.14, so uv provisions one.
uv_release="$work/uv-release"
uv_root="$work/uv-root"
uv_log="$work/uv.log"
uv_bin="$work/uv-bin"
mkdir -p "$uv_release/wheels" "$uv_bin"
build_wheel "$uv_release/wheels" methyl-pipeline 1.1.0 '>=3.10,<3.14' "$entry_scripts" >/dev/null
cat >"$uv_bin/python3" <<EOF
#!/bin/bash
if [[ "\$1" == "-c" && "\$2" == 'import sys; print("%d.%d.%d" % sys.version_info[:3])' ]]; then
  echo "3.14.4"
  exit 0
fi
exec /usr/bin/python3 "\$@"
EOF
cat >"$uv_bin/uv" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$uv_log"
if [[ "\$1" == "python" ]]; then
  exit 0
fi
if [[ "\$1" == "venv" ]]; then
  dest="\${@: -1}"
  /usr/bin/python3 -m venv "\$dest"
  exit 0
fi
echo "unexpected uv args: \$*" >&2
exit 1
EOF
chmod +x "$uv_bin/python3" "$uv_bin/uv"
: >"$uv_log"
PATH="$uv_bin:$PATH" bash "$SCRIPT" --root "$uv_root" --arch amd64 --release "$uv_release" >"$out" 2>"$err"
grep -q 'does not satisfy Requires-Python' "$out" || fail "3.14.4 should be rejected by >=3.10,<3.14"
grep -q 'python install 3.13' "$uv_log" || fail "uv should provision 3.13 when 3.14 is too new: $(cat "$uv_log")"
[[ -x "$uv_root/venv-amd64/bin/methyl-gateway" ]] || fail "uv path did not install methyl-gateway"
[[ -x "$uv_root/venv-amd64/bin/methyl-reclaim-leases" ]] || fail "uv path did not install methyl-reclaim-leases"

# python3 -m venv failure installs python3-venv with sudo, then fails closed if it still cannot.
apt_release="$work/apt-release"
apt_root="$work/apt-root"
apt_bin="$work/apt-bin"
sudo_log="$work/sudo.log"
mkdir -p "$apt_release/wheels" "$apt_bin"
build_wheel "$apt_release/wheels" methyl-pipeline 1.2.0 '>=3.10,<3.15' "$entry_scripts" >/dev/null
cat >"$apt_bin/python3" <<'EOF'
#!/bin/bash
if [[ "$1" == "-m" && "$2" == "venv" ]]; then
  echo "forced venv failure" >&2
  exit 1
fi
exec /usr/bin/python3 "$@"
EOF
cat >"$apt_bin/sudo" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$sudo_log"
exit 0
EOF
chmod +x "$apt_bin/python3" "$apt_bin/sudo"
: >"$sudo_log"
set +e
PATH="$apt_bin:$PATH" bash "$SCRIPT" --root "$apt_root" --arch amd64 --release "$apt_release" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "venv creation should fail when python3 -m venv keeps failing"
grep -q 'apt-get install -y python3-venv' "$sudo_log" || fail "python3-venv should be installed with sudo: $(cat "$sudo_log")"
grep -q 'python3-venv' "$err" || fail "venv failure should mention python3-venv"
[[ ! -x "$apt_root/venv-amd64/bin/methyl-gateway" ]] || fail "failed venv install must not claim the entry point exists"

bare="$work/bare-release"
bare_root="$work/bare-root"
mkdir -p "$bare/wheels"
build_wheel "$bare/wheels" methyl-pipeline 1.3.0 '>=3.10,<3.15' '' >/dev/null
set +e
bash "$SCRIPT" --root "$bare_root" --arch amd64 --release "$bare" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "a filename match without the console script must fail"
grep -q 'console script' "$err" || fail "missing console script should be named: $(cat "$err")"
grep -q 'entry_points.txt' "$err" || fail "discovery should read entry_points.txt"
grep -q '203/EXEC' "$err" || fail "missing entry point should mention status 203/EXEC"
[[ ! -x "$bare_root/venv-amd64/bin/methyl-gateway" ]] || fail "missing script wheel must not leave methyl-gateway"

# The core wheel may not be named methyl-pipeline or methyl-gateway.
odd="$work/odd-release"
odd_root="$work/odd-root"
mkdir -p "$odd/wheels"
build_wheel "$odd/wheels" methyl-pipeline 2.0.0 '>=3.10,<3.15' '' >/dev/null
build_wheel "$odd/wheels" corepkg 2.0.0 '>=3.10,<3.15' "$entry_scripts" >/dev/null
bash "$SCRIPT" --root "$odd_root" --arch amd64 --release "$odd" >"$out" 2>"$err"
grep -q 'corepkg-2.0.0' "$out" || fail "install should select the wheel by entry_points.txt, not the filename"
if grep -q 'methyl_pipeline-2.0.0' "$out"; then
  fail "filename methyl-pipeline without the console script must not be installed"
fi
[[ "$("$odd_root/venv-amd64/bin/methyl-gateway")" == "gateway" ]] || fail "corepkg methyl-gateway did not run"

req_release="$work/req-release"
req_root="$work/req-root"
mkdir -p "$req_release/wheels"
build_wheel "$req_release/wheels" goliath-gateway-test-dep 1.0.0 '>=3.10,<3.15' '' >/dev/null
build_wheel "$req_release/wheels" corepkg 3.0.0 '>=3.10,<3.15' "$entry_scripts" 'goliath-gateway-test-dep' >/dev/null
printf '%s\n' 'goliath-gateway-test-dep' 'numpy' >"$req_release/requirements-pipeline.txt"
bash "$SCRIPT" --root "$req_root" --arch amd64 --release "$req_release" >"$out" 2>"$err"
grep -q 'requirements-pipeline.txt' "$out" || fail "relevant requirements-pipeline.txt lines should be installed"
"$req_root/venv-amd64/bin/python" -m pip show goliath-gateway-test-dep >/dev/null \
  || fail "gateway dependency wheel should be installed via find-links"
if "$req_root/venv-amd64/bin/python" -m pip show numpy >/dev/null 2>&1; then
  fail "requirements-pipeline.txt packages that are not gateway dependencies must not be installed"
fi

set +e
bash "$SCRIPT" --root /work/goliath --release "$release" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "gateway root on /work must be rejected"
grep -q '/work' "$err" || fail "rejection should mention /work"

set +e
bash "$SCRIPT" --root "$work/empty-root" --arch amd64 >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "missing release and missing entry point must fail"
grep -q '203/EXEC' "$err" || fail "missing release should say the service would fail 203/EXEC"

mkdir -p "$work/prebuilt/venv-amd64/bin"
cp "$root/venv-amd64/bin/methyl-gateway" "$work/prebuilt/venv-amd64/bin/methyl-gateway"
cp "$root/venv-amd64/bin/methyl-reclaim-leases" "$work/prebuilt/venv-amd64/bin/methyl-reclaim-leases"
chmod +x "$work/prebuilt/venv-amd64/bin/methyl-gateway" "$work/prebuilt/venv-amd64/bin/methyl-reclaim-leases"
bash "$SCRIPT" --root "$work/prebuilt" --arch amd64 >"$out" 2>"$err"
grep -q 'already installed' "$out" || fail "existing entry points without a release should be kept"

only_worker="$work/only-worker"
mkdir -p "$only_worker/wheels"
build_wheel "$only_worker/wheels" methyl-worker 9.0.0 '>=3.10,<3.15' '' >/dev/null
set +e
bash "$SCRIPT" --root "$work/only-worker-root" --arch amd64 --release "$only_worker" >"$out" 2>"$err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "a release without the methyl-gateway console script must fail"
grep -q 'console script' "$err" || fail "missing wheel error should mention the console script"

rm -f "$out" "$err"
echo "test_install_gateway_venv ok"
