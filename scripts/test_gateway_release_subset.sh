#!/bin/bash
# The gateway host is only the REST API gateway. deploy_target.sh sends it
# gateway-dist: the methyl-gateway/reclaim wheels, their dependency wheels and
# a requirements file filtered to those deps. Never the release bundle
# (worker wheels, CUDA/RAPIDS, extractor, runtime-bundle, manifest), never
# worker scripts, and no <root>/release staging dir.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
src="$work/release"
mkdir -p "$src/wheels" "$src/runtime-bundle/scripts"

# whl DIR NAME VERSION "REQUIRES,..." "SCRIPT=mod:fn;..."
whl() {
  python3 - "$@" <<'PY'
import sys, zipfile
from pathlib import Path
dest, name, ver, reqs, scripts = sys.argv[1:6]
norm = name.replace("-", "_")
di = f"{norm}-{ver}.dist-info"
meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n"
for r in filter(None, reqs.split(",")):
    meta += f"Requires-Dist: {r}\n"
with zipfile.ZipFile(Path(dest) / f"{norm}-{ver}-py3-none-any.whl", "w") as zf:
    zf.writestr(f"{di}/METADATA", meta)
    if scripts:
        zf.writestr(f"{di}/entry_points.txt", "[console_scripts]\n" + "\n".join(scripts.split(";")) + "\n")
PY
}
whl "$src/wheels" methyl-gateway 2026.10.12 "methyl-domain>=1,fastapi,pytest; extra == 'test'" \
  "methyl-gateway=rest.gateway:main;methyl-reclaim-leases=ops.reclaim:main"
whl "$src/wheels" methyl-domain 1.0.0 "pydantic" ""
whl "$src/wheels" pydantic 2.0.0 "" ""
whl "$src/wheels" methyl-worker 1.0.0 "methyl-domain" "methyl-worker=w:main"
whl "$src/wheels" pytest 8.0.0 "" ""
head -c 2000000 /dev/zero >"$src/wheels/libcudf_cu12-26.4.0-py3-none-manylinux_2_28_x86_64.whl"
head -c 1000 /dev/zero >"$src/goliath-extractor-linux-amd64.tar.gz"
printf '{"version": "2026.10.12"}\n' >"$src/manifest.json"
printf 'fastapi>=0.110\nnumpy\npydantic>=2 # pinned\n' >"$src/requirements-pipeline.txt"
cp "$src/requirements-pipeline.txt" "$src/runtime-bundle/"
: >"$src/requirements-worker.lock"
: >"$src/runtime-bundle/scripts/promote_release.sh"

out="$work/out"
python3 "$ROOT/scripts/gateway_release_subset.py" "$src" "$out" >"$work/log" || fail "subset failed"
got="$(cd "$out" && find . -type f | sort | tr '\n' ' ')"
want="./requirements-pipeline.txt ./wheels/methyl_domain-1.0.0-py3-none-any.whl ./wheels/methyl_gateway-2026.10.12-py3-none-any.whl ./wheels/pydantic-2.0.0-py3-none-any.whl "
[[ "$got" == "$want" ]] || fail "unexpected gateway subset: $got"
grep -q '3 of 5 wheels' "$work/log" || { cat "$work/log"; fail "summary should count wheels"; }
[[ "$(grep -v '^#' "$out/requirements-pipeline.txt" | tr '\n' ' ')" == "fastapi>=0.110 pydantic>=2 " ]] \
  || fail "requirements should keep only gateway deps: $(cat "$out/requirements-pipeline.txt")"

# The selected subset still satisfies install_gateway_venv.sh's wheel finder.
grep -q 'release.rglob("\*.whl")' "$ROOT/scripts/install_gateway_venv.sh" || fail "gateway venv finder changed; recheck subset"

# No methyl-gateway wheel: fail clearly.
rm -f "$src/wheels/methyl_gateway-"*.whl
if python3 "$ROOT/scripts/gateway_release_subset.py" "$src" "$work/out2" 2>"$work/err"; then
  fail "subset without methyl-gateway must fail"
fi
grep -q 'methyl-gateway console script' "$work/err" || fail "error should name the console script"

# deploy_target.sh wiring.
d="$ROOT/scripts/deploy_target.sh"
grep -q 'stage_gateway_release "\$dest" "\$root"' "$d" || fail "gateway must use stage_gateway_release"
grep -q 'release_remote="\$root/gateway-dist"' "$d" || fail "gateway installs from <root>/gateway-dist"
grep -q 'install_remote_tree "\$dest" gateway' "$d" || fail "gateway gets the gateway-only script tree"
if sed -n '/^run_gateway() {/,/^}/p' "$d" | grep -q 'stage_release_dir'; then
  fail "the gateway must never receive the release bundle"
fi
gw_tree="$(sed -n '/^install_remote_tree() {/,/^  else/p' "$d")"
for bad in promote_release install_release install_worker_systemd methyl-worker setup_gpu_node install_methyl_extractor; do
  if grep -q "$bad" <<<"$gw_tree"; then fail "gateway script tree must not ship $bad"; fi
done
grep -q -- '-xzf - -C' "$d" || fail "gateway archive must stream into tar, not the user /tmp"
grep -q 'not enough space on' "$d" || fail "gateway staging must check free space"
grep -qF 'for old in \"\$R/release\" \"\$R/releases\"' "$d" || fail "legacy <root>/release must be removed"
# Pod/runtime path still stages the full release to the shared /work tree.
grep -q 'stage_release_dir "\$dest" "\$release"' "$d" || fail "runtime staging must keep the full release"

echo "test_gateway_release_subset: OK"
