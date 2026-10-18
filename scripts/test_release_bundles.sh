#!/usr/bin/env bash
# Release-bundle tests (no network):
#  1. scripts/system_tools.list is complete: every external tool the worker
#     code and start scripts look up is in it (or explicitly exempt below).
#  2. check_offline_wheels.sh fails when a locked wheel, or a dependency of
#     one, is missing from wheels/, and passes when all are there.
#  3. bundle_sdist_wheels.py builds a wheel from an sdist-only pin and adds
#     its hash to the lock (skipped when setuptools is unavailable).
#  4. bundle_system_deps.sh bundles the FULL closure (also what the build
#     image already has) from release+updates+security as a verified flat
#     apt repository, and refuses an incomplete or non-self-contained one
#     (apt stubbed, GOLIATH_BUNDLE_IN_HOST=1).
#  5. check_system_deps_bundle.sh checks stock, oldest and worker images
#     offline and fails on any failure (container runtime stubbed).

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
# The apt-get stub behaves like apt for what bundle_system_deps.sh relies
# on: `update` fetches lists for the Suites in the private sources;
# `--download-only install` downloads the closure MINUS what the dpkg status
# it is given lists as installed (the build image's packages; the old bug);
# `-s install` from the finished bundle fails when a Depends is not in it.
B="$TMP/bstub"; mkdir -p "$B"
cat >"$B/apt-get" <<'S'
#!/usr/bin/env bash
archives="" lists="" status="/var/lib/dpkg/status" parts="" srclist="" dl=0 sim=0 action="" skip=0 pk=()
for a in "$@"; do
  if [[ "$skip" == 1 ]]; then
    skip=0
    case "$a" in
      Dir::Cache::archives=*) archives="${a#*=}" ;;
      Dir::State::Lists=*) lists="${a#*=}" ;;
      Dir::State::status=*) status="${a#*=}" ;;
      Dir::Etc::SourceParts=*) parts="${a#*=}" ;;
      Dir::Etc::SourceList=*) srclist="${a#*=}" ;;
    esac
    continue
  fi
  case "$a" in -o) skip=1 ;; --download-only) dl=1 ;; -s) sim=1 ;; update|install) action="$a" ;; -*) ;; *) pk+=("$a") ;; esac
done
if [[ "$action" == update ]]; then
  if grep -qs 'file:' "$srclist"; then exit 0; fi
  cat "$parts"/*.sources >"$BUNDLE_LOG.sources"
  for s in $(sed -n 's/^Suites: //p' "$parts"/*.sources); do
    [[ "$s" == "${FAKE_DROP_SUITE:-}" ]] && continue
    : >"$lists/mirror_dists_${s}_main_binary-amd64_Packages"
  done
  exit 0
fi
if [[ "$dl" == 1 ]]; then
  echo "status=$status" >>"$BUNDLE_LOG"
  for spec in $BUNDLE_CLOSURE; do
    p="${spec%%:*}"
    if [[ -s "$status" || "$status" == /var/lib/dpkg/status ]] && [[ " $BUILD_IMAGE_HAS " == *" $p "* ]]; then continue; fi
    deps=""; [[ "$spec" == *:* ]] && deps="${spec#*:}"
    { printf 'Package: %s\nVersion: 1.0\nArchitecture: amd64\n' "$p"; [[ -z "$deps" ]] || printf 'Depends: %s\n' "${deps//+/, }"; } \
      >"$archives/${p}_1.0_amd64.deb"
  done
  exit 0
fi
if [[ "$sim" == 1 ]]; then
  repo="$(sed -n 's/.*file:\([^ ]*\) .*/\1/p' "$srclist")"
  [[ -n "$repo" && -s "$repo/Packages" ]] || { echo "E: no bundle repository" >&2; exit 100; }
  have=" $(sed -n 's/^Package: //p' "$repo/Packages" | tr '\n' ' ') "
  for p in "${pk[@]}"; do [[ "$have" == *" $p "* ]] || { echo "E: Unable to locate package $p"; exit 100; }; done
  for d in $(sed -n 's/^Depends: //p' "$repo/Packages" | tr -d ' ' | tr ',' ' '); do
    [[ "$have" == *" $d "* ]] || { echo " x : Depends: $d but it is not installable"; exit 100; }
  done
  exit 0
fi
S
printf '#!/usr/bin/env bash\necho amd64\n' >"$B/dpkg"
cat >"$B/dpkg-deb" <<'S'
#!/usr/bin/env bash
# dpkg-deb -f FILE [FIELD]
if [[ -n "${3:-}" ]]; then sed -n "s/^$3: //p" "$2"; else cat "$2"; fi
S
chmod +x "$B"/*
printf 'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n' >"$TMP/os-release"
APT_PKGS="$(sed -e 's/#.*//' "$MANIFEST" | awk 'NF {print $2}' | sort -u | tr '\n' ' ')"
bundle() {  # bundle CLOSURE [BUILD_IMAGE_HAS]
  env PATH="$B:$PATH" GOLIATH_BUNDLE_IN_HOST=1 GOLIATH_OS_RELEASE="$TMP/os-release" BUNDLE_LOG="$TMP/apt.log" \
    BUNDLE_CLOSURE="$1" BUILD_IMAGE_HAS="${2:-}" FAKE_DROP_SUITE="${FAKE_DROP_SUITE:-}" \
    bash "$ROOT/scripts/bundle_system_deps.sh" --target ubuntu-24.04-amd64 --output "$TMP/sd" >"$TMP/bundle.out" 2>&1
}
# The full closure, including what the build image (stock ubuntu:24.04)
# already has: libtinfo6 at the -updates build, which libncurses6 needs
# exactly. The runpod image had an older libtinfo6, and the old bundle left
# it out because the build image had it.
CLOSURE="samtools:libhts3t64+libncurses6 bedtools fastp pigz acl:libacl1 libacl1 curl python3 python3.12 coreutils gzip tar libhts3t64 libncurses6:libtinfo6 libtinfo6"
rm -f "$TMP/apt.log"
bundle "$CLOSURE" "coreutils gzip tar libtinfo6 libacl1" || { cat "$TMP/bundle.out"; fail "bundle should succeed"; }
D="$TMP/sd/ubuntu-24.04-amd64"
grep -q '^status=/' "$TMP/apt.log" && ! grep -q '^status=/var/lib/dpkg/status$' "$TMP/apt.log" \
  || { cat "$TMP/apt.log"; fail "the closure must be resolved against a private (empty) dpkg status"; }
for p in libtinfo6 libacl1 coreutils gzip tar; do
  ls "$D/${p}_"*.deb >/dev/null 2>&1 || fail "$p must be bundled even though the build image has it (full closure)"
done
for s in "Suites: noble noble-updates" "Suites: noble-security"; do
  grep -q "$s" "$TMP/apt.log.sources" || { cat "$TMP/apt.log.sources"; fail "resolver sources must include: $s"; }
done
(cd "$D" && sha256sum --check --quiet --strict SHA256SUMS) || fail "bundle checksums"
grep -q ' Packages$' "$D/SHA256SUMS" && grep -q ' Release$' "$D/SHA256SUMS" || fail "SHA256SUMS must cover Packages and Release"
[[ "$(grep -c '^Filename: ' "$D/Packages")" == "$(find "$D" -name '*.deb' | wc -l)" ]] || fail "Packages must index every deb"
grep -q "^ $(sha256sum "$D/Packages" | cut -d' ' -f1) [0-9]* Packages$" "$D/Release" || { cat "$D/Release"; fail "Release must pin the Packages checksum"; }
jq -e '.format == "deb-repo" and .arch == "amd64" and (.packages | index("acl")) != null and .debs.libtinfo6 == "1.0" and .deb_count == 15 and (has("preinstalled") | not)' \
  "$D/bundle.json" >/dev/null || { cat "$D/bundle.json"; fail "bundle.json"; }
for p in $APT_PKGS; do ls "$D/${p}_"*.deb >/dev/null 2>&1 || fail "apt package $p not bundled"; done
# A manifest package the resolver did not deliver fails the release.
if bundle "${CLOSURE/python3.12 /}"; then fail "incomplete bundle must fail"; fi
grep -qE 'python3.12 is missing from the bundle|Unable to locate package python3.12' "$TMP/bundle.out" || { cat "$TMP/bundle.out"; fail "incomplete bundle message"; }
# A closure that is not self-contained (a Depends outside the set) fails.
if bundle "${CLOSURE/libncurses6:libtinfo6 libtinfo6/libncurses6:libtinfo6}"; then fail "non-self-contained bundle must fail"; fi
grep -q 'not self-contained' "$TMP/bundle.out" || { cat "$TMP/bundle.out"; fail "self-containment message"; }
# A missing pocket (-security not reachable) fails instead of bundling stale debs.
if FAKE_DROP_SUITE=noble-security bundle "$CLOSURE"; then fail "missing -security lists must fail"; fi
grep -q 'no package lists for noble-security' "$TMP/bundle.out" || { cat "$TMP/bundle.out"; fail "missing pocket message"; }
echo "ok: system deps bundle"

# ---------- 5. offline install check (release gate) ----------
# check_system_deps_bundle.sh with a stub container runtime: it must run the
# stock, oldest and worker images with --network none and fail the release
# when an image cannot install the bundle offline.
R="$TMP/rtstub"; mkdir -p "$R"
cat >"$R/rt" <<'S'
#!/usr/bin/env bash
echo "rt $*" >>"$RT_LOG"
case "$1" in
  pull|commit|rm|rmi) exit 0 ;;
  image) [[ -z "${RT_ABSENT:-}" ]] ;  exit ;;
  manifest) printf '[{"Descriptor":{"platform":{"architecture":"amd64","os":"linux"}},"OCIManifest":{"layers":[{"size":%s}]}}]\n' "${RT_IMAGE_BYTES:-1000}"; exit 0 ;;
  info) echo /; exit 0 ;;
  run)
    shift; img=""; net=1
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --rm) shift ;;
        --network) [[ "$2" == none ]] && net=0; shift 2 ;;
        --name|--platform|--user|--entrypoint|-v|-e) shift 2 ;;
        *) img="$1"; break ;;
      esac
    done
    if [[ "$img" == ubuntu:24.04 && "$net" == 1 ]]; then echo "libc6 2.39-0ubuntu8"; exit 0; fi  # oldest prep
    [[ "$net" == 0 ]] || { echo "check ran with network" >&2; exit 1; }
    [[ " ${RT_FAIL_IMAGES:-} " == *" $img "* ]] && { echo "GOLIATH-FAIL samtools is not on PATH"; exit 1; }
    [[ " ${RT_OTHER_OS:-} " == *" $img "* ]] && { echo "GOLIATH-SKIP image is ubuntu-22.04-amd64"; exit 0; }
    echo "ok: samtools: samtools 1.19"; exit 0 ;;
esac
exit 1
S
chmod +x "$R/rt"
check() {
  env GOLIATH_CONTAINER_RUNTIME="$R/rt" RT_LOG="$TMP/rt.log" "$@" \
    bash "$ROOT/scripts/check_system_deps_bundle.sh" --bundle "$TMP/sd" --target ubuntu-24.04-amd64 >"$TMP/check.out" 2>&1
}
bundle "$CLOSURE" || fail "rebuild bundle"
rm -f "$TMP/rt.log"
check RT_FAIL_IMAGES= || { cat "$TMP/check.out"; fail "check should pass"; }
grep -q 'run --rm --network none .* ubuntu:24.04 ' "$TMP/rt.log" || { cat "$TMP/rt.log"; fail "stock image must be checked offline"; }
grep -q 'run --rm --network none .* goliath-sysdeps-oldest:ubuntu-24.04-amd64 ' "$TMP/rt.log" || fail "oldest-noble image must be checked offline"
WORKER_IMG="$(jq -r '[.workers[] | select(.arch == "amd64") | .image] | first' "$ROOT/deploy/targets/goliath-test.json")"
grep -qF "run --rm --network none --platform linux/amd64 --user 0 --entrypoint bash -v $TMP/sd:/goliath-bundle:ro" "$TMP/rt.log" || fail "bundle must be mounted read-only"
grep -qF " $WORKER_IMG -c" "$TMP/rt.log" || { cat "$TMP/rt.log"; fail "the deploy target worker image ($WORKER_IMG) must be checked by default"; }
if check RT_FAIL_IMAGES="$WORKER_IMG"; then fail "a worker image that cannot install the bundle must fail the release"; fi
grep -q "does not install offline in $WORKER_IMG" "$TMP/check.out" || { cat "$TMP/check.out"; fail "worker failure message"; }
if check RT_FAIL_IMAGES="goliath-sysdeps-oldest:ubuntu-24.04-amd64"; then fail "the oldest-noble failure must fail the release"; fi
check GOLIATH_SYSDEPS_CHECK_IMAGES="example/other:1" RT_OTHER_OS="example/other:1" || { cat "$TMP/check.out"; fail "other-OS image should be skipped"; }
grep -q 'WARNING: .*example/other:1 is not ubuntu-24.04-amd64' "$TMP/check.out" || { cat "$TMP/check.out"; fail "other-OS skip warning"; }
# A worker image too large for the free container storage: skipped with a
# warning (stock + oldest still gate), or fatal with --require-worker-images.
huge=$((1024 * 1024 * 1024 * 1024 * 64))
check RT_ABSENT=1 RT_IMAGE_BYTES="$huge" || { cat "$TMP/check.out"; fail "a worker image that does not fit should be skipped"; }
grep -q "WARNING: not enough container storage to pull worker image $WORKER_IMG" "$TMP/check.out" || { cat "$TMP/check.out"; fail "disk skip warning"; }
if check RT_ABSENT=1 RT_IMAGE_BYTES="$huge" GOLIATH_SYSDEPS_REQUIRE_WORKER_IMAGES=1; then fail "--require-worker-images must fail when the image does not fit"; fi
check RT_ABSENT=1 RT_IMAGE_BYTES=1000 || { cat "$TMP/check.out"; fail "a small absent worker image should be pulled and checked"; }
grep -q "pulling $WORKER_IMG" "$TMP/check.out" || { cat "$TMP/check.out"; fail "worker image pull"; }
grep -q "rt rmi -f $WORKER_IMG" "$TMP/rt.log" || fail "a pulled worker image must be removed afterwards"
echo "ok: offline install check"

echo "PASS: release_bundles"
