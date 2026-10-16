#!/bin/bash
# worker_venv_local.sh builds the worker venv on local disk from a release
# that sits on a (simulated) shared work mount. No network: the lock only
# names a wheel shipped in the release.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
python3 -m venv --help >/dev/null 2>&1 || { echo "SKIP: python3 -m venv unavailable"; exit 0; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mount="$work/work"
local_root="$work/local"
release="$mount/goliath/releases/9.9.9"
mkdir -p "$release/wheels" "$local_root"
printf '{"version": "9.9.9"}\n' >"$release/manifest.json"

# make_wheel DEST NAME VERSION [ENTRY_POINT]
make_wheel() {
python3 - "$@" <<'PY'
import base64, hashlib, sys, zipfile
from pathlib import Path
dest = Path(sys.argv[1])
name, ver = sys.argv[2], sys.argv[3]
entry = sys.argv[4] if len(sys.argv) > 4 else ""
dist = name.replace("_", "-")
files = {
    f"{name}/__init__.py": "def main():\n    print('worker-ok')\n",
    f"{name}-{ver}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: {ver}\n",
    f"{name}-{ver}.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
}
if entry:
    files[f"{name}-{ver}.dist-info/entry_points.txt"] = f"[console_scripts]\n{entry} = {name}:main\n"
record = []
for path, body in files.items():
    digest = base64.urlsafe_b64encode(hashlib.sha256(body.encode()).digest()).rstrip(b"=").decode()
    record.append(f"{path},sha256={digest},{len(body.encode())}")
record.append(f"{name}-{ver}.dist-info/RECORD,,")
files[f"{name}-{ver}.dist-info/RECORD"] = "\n".join(record) + "\n"
with zipfile.ZipFile(dest / f"{name}-{ver}-py3-none-any.whl", "w") as zf:
    for path, body in files.items():
        zf.writestr(path, body)
PY
}
make_wheel "$release/wheels" goliath_fake_worker 1.0.0 methyl-worker
printf 'goliath-fake-worker==1.0.0\n' >"$release/requirements-worker.lock"

run_install() {
  env GOLIATH_WORK_MOUNT="$mount" GOLIATH_LOCAL_ROOT="$local_root" \
    GOLIATH_VENV_UPGRADE_PIP=0 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHON_BIN=python3 \
    bash "$ROOT/scripts/worker_venv_local.sh" --release "$release" --arch amd64 --print-dir "$@"
}

out="$(run_install 2>"$work/err1")" || { cat "$work/err1" >&2; fail "first install failed"; }
venv="$local_root/venv-amd64"
[[ "$out" == "$venv" ]] || fail "venv dir should be $venv, got $out"
[[ -x "$venv/bin/methyl-worker" ]] || fail "methyl-worker entry point missing"
[[ "$("$venv/bin/methyl-worker")" == "worker-ok" ]] || fail "methyl-worker did not run"
grep -q '^version=9.9.9$' "$venv/.goliath-venv-stamp" || fail "stamp should record the release version"
grep -q '^lock_sha256=' "$venv/.goliath-venv-stamp" || fail "stamp should record the lock sha"
[[ ! -e "$mount/goliath/venv-amd64" ]] || fail "nothing may be built on the work mount"
if find "$mount" -name 'python*' -path '*/bin/*' | grep -q .; then
  fail "an interpreter was written under the work mount"
fi
if ls -d "$local_root"/.venv-stage-* >/dev/null 2>&1; then
  fail "staging copy should be removed"
fi

run_install >/dev/null 2>"$work/err2" || { cat "$work/err2" >&2; fail "second install failed"; }
grep -q 'already matches release 9.9.9' "$work/err2" || fail "unchanged release should skip the install"

printf '# changed\ngoliath-fake-worker==1.0.0\n' >"$release/requirements-worker.lock"
run_install >/dev/null 2>"$work/err3" || { cat "$work/err3" >&2; fail "lock change install failed"; }
grep -q 'installing worker venv' "$work/err3" || fail "a changed lock should rebuild the venv"
[[ "$("$venv/bin/methyl-worker")" == "worker-ok" ]] || fail "rebuilt methyl-worker did not run"

# A local root on the work mount falls back to /tmp.
dir="$(env GOLIATH_WORK_MOUNT="$mount" GOLIATH_LOCAL_ROOT="$mount/goliath" \
  bash "$ROOT/scripts/worker_venv_local.sh" --arch amd64 --print-dir 2>"$work/err4")"
[[ "$dir" == "/tmp/goliath/venv-amd64" ]] || fail "work-mount local root should fall back to /tmp, got $dir"
grep -q 'shared work mount' "$work/err4" || fail "fallback should warn"

# install_release.sh refuses a venv on the work mount.
set +e
env GOLIATH_WORK_MOUNT="$mount" bash "$ROOT/scripts/install_release.sh" \
  --release-dir "$release" --venv "$mount/goliath/venv-amd64" >"$work/out5" 2>&1
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "install_release.sh must refuse a venv on the work mount"
grep -q 'shared work mount' "$work/out5" || fail "refusal should name the work mount"
[[ ! -e "$mount/goliath/venv-amd64" ]] || fail "refused install must not create the venv"

# A wheel that does not read back fails the install instead of using a bad copy.
unreadable="$release/wheels/broken-0.0.0-py3-none-any.whl"
printf 'x' >"$unreadable"
chmod 000 "$unreadable"
if [[ ! -r "$unreadable" ]]; then
  set +e
  GOLIATH_COPY_RETRY_SLEEP=0 run_install --recreate >/dev/null 2>"$work/err6"
  status=$?
  set -e
  [[ "$status" -ne 0 ]] || fail "unreadable wheel should fail the install"
  grep -q 'cannot read' "$work/err6" || fail "unreadable wheel should be named"
fi
chmod 600 "$unreadable"

rm -f "$unreadable"

# A lock carrying a baked-in build-host find-links still installs offline from
# the release wheels only, and pip lists the local wheels dir exactly once.
printf -- '--find-links /home/runner/work/_temp/release/wheels\ngoliath-fake-worker==1.0.0\n' \
  >"$release/requirements-worker.lock"
run_install --recreate >/dev/null 2>"$work/err7" || { cat "$work/err7" >&2; fail "lock with runner find-links failed"; }
if grep -q '/home/runner' "$work/err7"; then
  cat "$work/err7" >&2; fail "runner find-links path must not reach pip"
fi
n="$(grep -o 'Looking in links: .*' "$work/err7" | head -1 | tr ',' '\n' | grep -c 'wheels' || true)"
[[ "$n" -le 1 ]] || { cat "$work/err7" >&2; fail "wheels dir should be listed once in find-links, got $n"; }

# A third-party pin missing from wheels/: offline fails, the warning is
# logged, and the retry pulls it from the (local file) index, still pinned.
index="$work/index"
mkdir -p "$index/annotated-types"
make_wheel "$index/annotated-types" annotated_types 0.8.0
printf '<a href="annotated_types-0.8.0-py3-none-any.whl">annotated_types-0.8.0-py3-none-any.whl</a>\n' \
  >"$index/annotated-types/index.html"
printf 'goliath-fake-worker==1.0.0\nannotated-types==0.8.0\n' >"$release/requirements-worker.lock"
run_fallback() {
  env GOLIATH_WORK_MOUNT="$mount" GOLIATH_LOCAL_ROOT="$local_root" \
    GOLIATH_VENV_UPGRADE_PIP=0 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHON_BIN=python3 \
    GOLIATH_PIP_INDEX_URL="file://$index" "$@" \
    bash "$ROOT/scripts/worker_venv_local.sh" --release "$release" --arch amd64 --recreate
}
run_fallback >/dev/null 2>"$work/err8" || { cat "$work/err8" >&2; fail "index fallback install failed"; }
grep -q 'offline install from release wheels/ failed; retrying' "$work/err8" || fail "fallback should warn"
"$venv/bin/python" -c 'import annotated_types' || fail "fallback should install annotated-types"
[[ ! -e "$mount/goliath/venv-amd64" ]] || fail "fallback must not build on the work mount"

set +e
run_fallback GOLIATH_WORKER_VENV_ALLOW_INDEX=0 >/dev/null 2>"$work/err9"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "GOLIATH_WORKER_VENV_ALLOW_INDEX=0 must not go online"
grep -q 'ALLOW_INDEX=0' "$work/err9" || fail "offline-only failure should say why"

# verify_locked_wheels.py: pure wheels count for every arch, a missing pin fails.
vdir="$work/verify"
mkdir -p "$vdir"
make_wheel "$vdir" goliath_fake_worker 1.0.0
: >"$vdir/numpy-2.1.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
printf 'goliath-fake-worker==1.0.0 \\\n    --hash=sha256:00\nnumpy==2.1.0\n' >"$vdir/lock"
if python3 "$ROOT/scripts/verify_locked_wheels.py" "$vdir/lock" "$vdir" 2>"$work/err10"; then
  fail "numpy without an aarch64 wheel should fail verification"
fi
grep -q 'MISSING numpy==2.1.0 for linux aarch64' "$work/err10" || { cat "$work/err10" >&2; fail "missing arch should be named"; }
python3 "$ROOT/scripts/verify_locked_wheels.py" "$vdir/lock" "$vdir" --arch x86_64 2>/dev/null \
  || fail "x86_64 should verify"
: >"$vdir/numpy-2.1.0-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
python3 "$ROOT/scripts/verify_locked_wheels.py" "$vdir/lock" "$vdir" 2>/dev/null || fail "both arches should verify"
grep -q 'no-emit-find-links' "$ROOT/scripts/build_release.sh" || fail "build_release must not emit find-links into the lock"

# promote_release.sh no longer builds a venv under the root.
grep -q -- '--skip-venv' "$ROOT/scripts/start_shared_worker.sh" || fail "pod boot promote must pass --skip-venv"
if grep -n 'venv-\${ARCH}\|venv-amd64' "$ROOT/scripts/promote_release.sh" "$ROOT/scripts/write_worker_env.sh" \
  "$ROOT/scripts/start_shared_worker.sh" "$ROOT/scripts/install_worker_systemd.sh" \
  "$ROOT/scripts/provision_worker_node.sh" | grep -v 'stale\|Older\|_wvl_on_work\|rm -rf'; then
  fail "worker scripts still reference \$ROOT/venv-<arch>"
fi

echo "test_worker_venv_local: OK"
