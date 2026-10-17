#!/usr/bin/env bash
# Release-bundle tests (no network):
#  1. scripts/system_tools.list is complete: every external tool the worker
#     code and start scripts look up is in it (or explicitly exempt below).
#  2. check_offline_wheels.sh fails when a locked wheel, or a dependency of
#     one, is missing from wheels/, and passes when all are there.
#  3. bundle_sdist_wheels.py builds a wheel from an sdist-only pin and adds
#     its hash to the lock (skipped when setuptools is unavailable).
#  4. bundle_system_deps.sh writes a verified .deb bundle and refuses an
#     incomplete one (apt stubbed, GOLIATH_BUNDLE_IN_HOST=1).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
PY="${PYTHON_BIN:-python3}"
MANIFEST="$ROOT/scripts/system_tools.list"

# ---------- 1. manifest completeness ----------
mapfile -t TOOLS < <(sed -e 's/#.*//' "$MANIFEST" | awk 'NF {print $1}')
while read -r line; do
  line="${line%%#*}"; read -r -a f <<<"$line" || true
  [[ "${#f[@]}" -eq 0 ]] && continue
  [[ "${#f[@]}" -eq 4 ]] || fail "manifest line must have 4 columns: '$line'"
  [[ "${f[1]}" != - ]] || fail "${f[0]} needs an apt package (workers run Ubuntu)"
done <"$MANIFEST"
in_manifest() { local t; for t in "${TOOLS[@]}"; do [[ "$t" == "$1" ]] && return 0; done; return 1; }
for must in samtools bedtools setfacl fastp pigz; do
  in_manifest "$must" || fail "manifest lacks $must"
done
grep -qE '^setfacl[[:space:]]+acl[[:space:]]' "$MANIFEST" || fail "setfacl must come from the acl package"

# Tools that are deliberately not host packages, with the reason.
EXEMPT=(
  docker        # container runtime, provided by the image/provider when GPU tools need it
  nvidia-smi    # NVIDIA driver, provided by the GPU host
  rocm-smi rocminfo  # AMD driver tools, provided by the host
  MethylExtractor    # release artifact installed by install_local_extractor
  methyl-validation methyl-detector methyl-cfg methyl-worker  # venv console scripts
  sage          # proteomics, Docker image or METHYL_SAGE_BIN
  vg            # inside the methylgrapher Docker image
  uv apptainer singularity systemctl  # optional host managers, probed with fallbacks
  methyl-worker # venv console script
  python3 curl tar gzip sha256sum     # listed in the manifest already (base tools)
)
exempt() { local t; for t in "${EXEMPT[@]}"; do [[ "$t" == "$1" ]] && return 0; done; return 1; }

found="$TMP/found.txt"
{
  # Python lookups of external binaries.
  grep -rhoE 'shutil\.which\("[A-Za-z0-9_.-]+"\)|_cli_on_path\("[A-Za-z0-9_.-]+"\)' \
    "$ROOT/workers/methyl_worker" "$ROOT/packages" --include='*.py' | sed -E 's/.*\("([^"]+)"\)/\1/'
  # Literal argv[0] of subprocess calls for well-known bio/compression tools.
  grep -rhoE "[\[(][[:space:]]*['\"](samtools|bedtools|bgzip|tabix|bcftools|pigz|fastp|setfacl)['\"]" \
    "$ROOT/workers/methyl_worker" "$ROOT/packages" --include='*.py' | sed -E "s/.*['\"]([^'\"]+)['\"]/\1/"
  # Shell lookups in the worker start path.
  grep -hoE 'command -v [A-Za-z0-9_.-]+' "$ROOT/scripts/start_shared_worker.sh" "$ROOT/scripts/init_work_layout.sh" \
    "$ROOT/scripts/worker_venv_local.sh" "$ROOT/deploy/runpod/goliath-worker-start.sh" \
    "$ROOT/deploy/runpod/goliath-worker-bootstrap.sh" | awk '{print $3}'
} | sort -u >"$found"
while read -r t; do
  [[ -n "$t" ]] || continue
  in_manifest "$t" || exempt "$t" || fail "worker code looks up '$t' but scripts/system_tools.list does not list it (add it, or exempt it with a reason in this test)"
done <"$found"
echo "ok: manifest covers $(wc -l <"$found") referenced tools"

# ---------- 2. offline wheel check ----------
mkwheel() {  # mkwheel DIR NAME VERSION [REQUIRES...]
  "$PY" - "$@" <<'PYW'
import base64, hashlib, sys, zipfile
d, name, ver, *reqs = sys.argv[1:]
dist = f"{name.replace('-', '_')}-{ver}"
files = {
    f"{name.replace('-', '_')}/__init__.py": b"",
    f"{dist}.dist-info/METADATA": ("Metadata-Version: 2.1\nName: %s\nVersion: %s\n%s" % (
        name, ver, "".join(f"Requires-Dist: {r}\n" for r in reqs))).encode(),
    f"{dist}.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
}
rec = []
for k, v in files.items():
    h = base64.urlsafe_b64encode(hashlib.sha256(v).digest()).rstrip(b"=").decode()
    rec.append(f"{k},sha256={h},{len(v)}")
rec.append(f"{dist}.dist-info/RECORD,,")
files[f"{dist}.dist-info/RECORD"] = ("\n".join(rec) + "\n").encode()
with zipfile.ZipFile(f"{d}/{dist}-py3-none-any.whl", "w") as z:
    for k, v in files.items():
        z.writestr(k, v)
PYW
}
W="$TMP/wheels"; mkdir -p "$W"
mkwheel "$W" alpha-pkg 1.0 "beta-pkg>=2"
mkwheel "$W" beta-pkg 2.0
mkwheel "$W" gamma-pkg 3.1
hashline() { printf '    --hash=sha256:%s' "$(sha256sum "$1" | cut -d' ' -f1)"; }
{
  printf 'alpha-pkg==1.0 \\\n%s\n' "$(hashline "$W/alpha_pkg-1.0-py3-none-any.whl")"
  printf 'beta-pkg==2.0 \\\n%s\n' "$(hashline "$W/beta_pkg-2.0-py3-none-any.whl")"
  printf 'gamma-pkg==3.1 \\\n%s\n' "$(hashline "$W/gamma_pkg-3.1-py3-none-any.whl")"
} >"$TMP/lock.txt"
check() { PYTHON_BIN="$PY" bash "$ROOT/scripts/check_offline_wheels.sh" "$TMP/lock.txt" "$W" --arch x86_64 --arch aarch64 >"$TMP/check.out" 2>&1; }
check || { cat "$TMP/check.out"; fail "complete wheels/ should pass the offline check"; }
mv "$W/gamma_pkg-3.1-py3-none-any.whl" "$TMP/"
if check; then fail "a missing locked wheel must fail the offline check"; fi
grep -q 'gamma-pkg' "$TMP/check.out" || { cat "$TMP/check.out"; fail "failure should name gamma-pkg"; }
if "$PY" "$ROOT/scripts/verify_locked_wheels.py" "$TMP/lock.txt" "$W" --strict --python-version 3.12 >/dev/null 2>&1; then
  fail "verify_locked_wheels --strict (the release default) must fail when a wheel is missing"
fi
grep -q 'STRICT_WHEELS="${GOLIATH_STRICT_WHEELS:-1}"' "$ROOT/scripts/build_release.sh" || fail "build_release.sh must default to strict wheels"
mv "$TMP/gamma_pkg-3.1-py3-none-any.whl" "$W/"
# A dependency missing from wheels/ (and from the lock) fails too.
grep -v -e '^beta-pkg' -e "$(sha256sum "$W/beta_pkg-2.0-py3-none-any.whl" | cut -d' ' -f1)" "$TMP/lock.txt" >"$TMP/lock2.txt"
mv "$W/beta_pkg-2.0-py3-none-any.whl" "$TMP/"
cp "$TMP/lock2.txt" "$TMP/lock.txt"
if check; then fail "a missing transitive dependency must fail the offline check"; fi
grep -q 'beta-pkg' "$TMP/check.out" || { cat "$TMP/check.out"; fail "failure should name beta-pkg"; }
echo "ok: offline wheel check"

# ---------- 3. sdist-only pin -> wheel + lock hash ----------
if "$PY" -c 'import setuptools, wheel' 2>/dev/null; then
  S="$TMP/sdist-src/onlysdist-0.16"; mkdir -p "$S/onlysdist" "$TMP/index" "$TMP/w3"
  printf 'from setuptools import setup\nsetup(name="onlysdist", version="0.16", packages=["onlysdist"])\n' >"$S/setup.py"
  : >"$S/onlysdist/__init__.py"
  printf 'Metadata-Version: 1.0\nName: onlysdist\nVersion: 0.16\n' >"$S/PKG-INFO"
  tar -C "$TMP/sdist-src" -czf "$TMP/index/onlysdist-0.16.tar.gz" onlysdist-0.16
  printf 'onlysdist==0.16 \\\n    --hash=sha256:%s\n' "$(sha256sum "$TMP/index/onlysdist-0.16.tar.gz" | cut -d' ' -f1)" >"$TMP/lock3.txt"
  PIP_NO_BUILD_ISOLATION=0 "$PY" "$ROOT/scripts/bundle_sdist_wheels.py" "$TMP/lock3.txt" "$TMP/w3" \
    --arch x86_64 --arch aarch64 --python "$PY" -- --no-index --find-links "$TMP/index" >"$TMP/sdist.out" 2>&1 \
    || { cat "$TMP/sdist.out"; fail "sdist-only pin should get a wheel"; }
  whl="$(ls "$TMP/w3"/onlysdist-0.16-py3-none-any.whl)" || fail "pure wheel not built"
  grep -q "sha256:$(sha256sum "$whl" | cut -d' ' -f1)" "$TMP/lock3.txt" || fail "built wheel hash must be added to the lock"
  PYTHON_BIN="$PY" bash "$ROOT/scripts/check_offline_wheels.sh" "$TMP/lock3.txt" "$TMP/w3" --arch x86_64 --arch aarch64 >/dev/null 2>&1 \
    || fail "built wheel should satisfy --require-hashes offline install"
  echo "ok: sdist-only pin bundled"
else
  echo "SKIP: setuptools/wheel not importable by $PY; sdist build case not run"
fi

# ---------- 4. system deps bundle ----------
B="$TMP/bstub"; mkdir -p "$B"
cat >"$B/apt-get" <<'S'
#!/usr/bin/env bash
archives="" dl=0
for a in "$@"; do
  case "$a" in Dir::Cache::archives=*) archives="${a#*=}" ;; --download-only) dl=1 ;; esac
done
[[ "$dl" == 1 ]] || exit 0
for p in $BUNDLE_CLOSURE; do printf 'Package: %s\n' "$p" >"$archives/${p}_1.0_amd64.deb"; done
S
printf '#!/usr/bin/env bash\necho amd64\n' >"$B/dpkg"
cat >"$B/dpkg-query" <<'S'
#!/usr/bin/env bash
for p in $BUNDLE_PREINSTALLED; do [[ "$p" == "${!#}" ]] && { printf 'install ok installed'; exit 0; }; done
exit 1
S
chmod +x "$B"/*
APT_PKGS="$(sed -e 's/#.*//' "$MANIFEST" | awk 'NF {print $2}' | sort -u | tr '\n' ' ')"
bundle() {
  env PATH="$B:$PATH" GOLIATH_BUNDLE_IN_HOST=1 BUNDLE_CLOSURE="$1" BUNDLE_PREINSTALLED="$2" \
    bash "$ROOT/scripts/bundle_system_deps.sh" --target ubuntu-24.04-amd64 --output "$TMP/sd" >"$TMP/bundle.out" 2>&1
}
# Closure: every non-base package plus a library dependency; base tools preinstalled.
bundle "samtools bedtools fastp pigz acl curl python3 python3.12 libhts3t64" "coreutils gzip tar" \
  || { cat "$TMP/bundle.out"; fail "bundle should succeed"; }
D="$TMP/sd/ubuntu-24.04-amd64"
(cd "$D" && sha256sum --check --quiet SHA256SUMS) || fail "bundle checksums"
jq -e '.format == "deb" and .arch == "amd64" and (.packages | index("acl")) != null and (.preinstalled | index("tar")) != null and .deb_count == 9' \
  "$D/bundle.json" >/dev/null || { cat "$D/bundle.json"; fail "bundle.json"; }
for p in $APT_PKGS; do
  ls "$D/${p}_"*.deb >/dev/null 2>&1 || jq -e --arg p "$p" '.preinstalled | index($p)' "$D/bundle.json" >/dev/null \
    || fail "apt package $p neither bundled nor preinstalled"
done
# A package the resolver did not deliver fails the release.
if bundle "samtools bedtools" "coreutils gzip tar"; then fail "incomplete bundle must fail"; fi
grep -q 'neither bundled nor preinstalled' "$TMP/bundle.out" || { cat "$TMP/bundle.out"; fail "incomplete bundle message"; }
echo "ok: system deps bundle"

echo "PASS: release_bundles"
