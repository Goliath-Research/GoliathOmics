#!/usr/bin/env bash
# Release-time proof that the offline system-deps bundle installs with NO
# network and that every tool in scripts/system_tools.list then runs.
#
#   check_system_deps_bundle.sh --bundle DIR --target ubuntu-24.04-amd64
#       [--image IMG]... [--require-worker-images] [--min-free-gb N]
#
# DIR is the release's system-deps/ (DIR/<target>/ from bundle_system_deps.sh).
# Each check copies install_system_deps.sh + system_tools.list next to the
# read-only bundle, runs `install_system_deps.sh --bundle` in a container
# started with --network none, refuses the online fallback, runs every
# manifest tool with --version and requires a clean `dpkg --audit`.
# Images checked, in order:
#   1. stock     <os>:<version> (e.g. ubuntu:24.04), current packages.
#   2. oldest    (Ubuntu) the stock image moved DOWN to the release pocket
#                (noble only, no -updates): the oldest package set any image
#                of this OS release can carry. This is the case that broke
#                on runpod/pytorch (libtinfo6 older than the bundled
#                libncurses6 needs). Built once with network, then checked
#                offline.
#   3. worker    every --image, default: the worker images of the deploy
#                targets (deploy/targets/*.json workers[].image whose arch
#                matches; env GOLIATH_SYSDEPS_CHECK_IMAGES overrides, ""
#                for none). An image of another OS release is skipped via
#                GOLIATH-SKIP inside the offline check (never a tools-only
#                pass). For ghcr.io/.../goliath-worker (private / may not
#                exist yet) the Clara NGC base is used when NGC_API_KEY is
#                set; otherwise that leg is skipped with a loud warning.
# Worker images can be large. A worker image that is not present locally is
# pulled only when the container storage has room (3x compressed + 5 GB;
# --min-free-gb, default 40); otherwise skipped with a warning unless
# --require-worker-images. Images this script pulled are removed afterwards
# (GOLIATH_SYSDEPS_CHECK_KEEP_IMAGES=1 keeps them).
#
# Needs docker or podman (GOLIATH_CONTAINER_RUNTIME). Exit 1 on any failure.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
MANIFEST="${GOLIATH_SYSTEM_TOOLS_MANIFEST:-$HERE/system_tools.list}"
BUNDLE="" TARGET="" REQUIRE_WORKER="${GOLIATH_SYSDEPS_REQUIRE_WORKER_IMAGES:-0}"
MIN_FREE_GB="${GOLIATH_SYSDEPS_CHECK_MIN_FREE_GB:-40}"
IMAGES=() IMAGES_SET=0

die() { printf '[check-system-deps] ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '[check-system-deps] %s\n' "$*" >&2; }
warn() {
  printf '[check-system-deps] WARNING: %s\n' "$*" >&2
  [[ -z "${GITHUB_ACTIONS:-}" ]] || printf '::warning title=system-deps check::%s\n' "$*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle) BUNDLE="${2:-}"; shift 2 ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --image) IMAGES+=("${2:-}"); IMAGES_SET=1; shift 2 ;;
    --require-worker-images) REQUIRE_WORKER=1; shift ;;
    --min-free-gb) MIN_FREE_GB="${2:-}"; shift 2 ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
[[ -n "$BUNDLE" && -n "$TARGET" ]] || die "--bundle and --target are required"
[[ "$TARGET" =~ ^([a-z]+)-([0-9.]+)-([a-z0-9_]+)$ ]] || die "--target must look like ubuntu-24.04-amd64 (got '$TARGET')"
OS_ID="${BASH_REMATCH[1]}" OS_VER="${BASH_REMATCH[2]}" ARCH="${BASH_REMATCH[3]}"
[[ -f "$BUNDLE/$TARGET/SHA256SUMS" ]] || die "no bundle at $BUNDLE/$TARGET"
[[ -f "$MANIFEST" ]] || die "tool manifest not found: $MANIFEST"
[[ "$MIN_FREE_GB" =~ ^[0-9]+$ ]] || die "--min-free-gb must be an integer"
BUNDLE="$(cd "$BUNDLE" && pwd)"
mapfile -t TOOLS < <(sed -e 's/#.*//' "$MANIFEST" | awk 'NF>=4 {print $1}')

RT="${GOLIATH_CONTAINER_RUNTIME:-}"
if [[ -z "$RT" ]]; then
  for c in docker podman; do command -v "$c" >/dev/null 2>&1 && { RT="$c"; break; }; done
fi
[[ -n "$RT" ]] || die "docker or podman is required to check the system-deps bundle"

if [[ "$IMAGES_SET" == 0 ]]; then
  if [[ -n "${GOLIATH_SYSDEPS_CHECK_IMAGES+x}" ]]; then
    read -r -a IMAGES <<<"$GOLIATH_SYSDEPS_CHECK_IMAGES"
  else
    mapfile -t IMAGES < <(python3 - "$REPO/deploy/targets" "$ARCH" <<'PY'
import glob, json, os, sys
d, want = sys.argv[1], sys.argv[2]
norm = {"x86_64": "amd64", "aarch64": "arm64"}
seen = []
for f in sorted(glob.glob(os.path.join(d, "*.json"))):
    if f.endswith((".example.json", ".schema.json")):
        continue
    try:
        workers = json.load(open(f)).get("workers") or []
    except Exception:
        continue
    for w in workers:
        if not isinstance(w, dict) or not w.get("image"):
            continue
        if norm.get(w.get("arch", "amd64"), w.get("arch", "amd64")) == want and w["image"] not in seen:
            seen.append(w["image"])
print("\n".join(seen))
PY
)
  fi
fi

WORK="$(mktemp -d)"
PULLED=() CHECKED=()
cleanup() {
  local i
  if [[ "${GOLIATH_SYSDEPS_CHECK_KEEP_IMAGES:-0}" != 1 ]]; then
    for i in "${PULLED[@]}"; do "$RT" rmi -f "$i" >/dev/null 2>&1 || true; done
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
mkdir -p "$WORK/scripts"
cp "$HERE/install_system_deps.sh" "$WORK/scripts/"
cp "$MANIFEST" "$WORK/scripts/system_tools.list"

# Runs inside the image, offline: install from the bundle, refuse the online
# fallback, then every tool must run.
CHECK='set -uo pipefail
want="$1"; shift
have="$(. /etc/os-release && printf "%s-%s" "${ID:-}" "${VERSION_ID:-}")-$(dpkg --print-architecture 2>/dev/null)"
if [[ "$have" != "$want" ]]; then echo "GOLIATH-SKIP image is $have"; exit 0; fi
GOLIATH_SYSDEPS_RETRIES=1 GOLIATH_SYSDEPS_RETRY_SECS=0 bash /goliath-scripts/install_system_deps.sh \
  --manifest /goliath-scripts/system_tools.list --bundle /goliath-bundle 2>&1 | tee /tmp/goliath-install.log
rc="${PIPESTATUS[0]}"
fail=0
[[ "$rc" == 0 ]] || { echo "GOLIATH-FAIL install_system_deps.sh exited $rc"; fail=1; }
if grep -q "installing from the network" /tmp/goliath-install.log; then echo "GOLIATH-FAIL the online fallback ran; the bundle did not cover this image"; fail=1; fi
for t in "$@"; do
  if ! command -v "$t" >/dev/null 2>&1; then echo "GOLIATH-FAIL $t is not on PATH"; fail=1
  elif ! out="$("$t" --version 2>&1 </dev/null)"; then echo "GOLIATH-FAIL $t --version: $(printf "%s" "$out" | tail -n 3)"; fail=1
  else echo "ok: $t: $(printf "%s" "$out" | head -n 1)"; fi
done
audit="$(dpkg --audit 2>&1)"; [[ -z "$audit" ]] || { echo "GOLIATH-FAIL dpkg --audit: $audit"; fail=1; }
exit "$fail"'

run_check() {  # run_check LABEL IMAGE
  local label="$1" img="$2" log="$WORK/check.log" rc
  info "[$label] installing the bundle offline (--network none) in $img"
  set +e
  "$RT" run --rm --network none --platform "linux/$ARCH" --user 0 --entrypoint bash \
    -v "$BUNDLE:/goliath-bundle:ro" -v "$WORK/scripts:/goliath-scripts:ro" \
    "$img" -c "$CHECK" check "$TARGET" "${TOOLS[@]}" >"$log" 2>&1
  rc=$?
  set -e
  sed "s/^/[check-system-deps] [$label]   /" "$log" >&2
  if grep -q '^GOLIATH-SKIP' "$log"; then
    warn "[$label] $img is not $TARGET ($(sed -n 's/^GOLIATH-SKIP image is //p' "$log")); not checked against this bundle"
    return 0
  fi
  [[ "$rc" == 0 ]] || die "[$label] the $TARGET bundle does not install offline in $img (see GOLIATH-FAIL above)"
  info "[$label] OK: all ${#TOOLS[@]} tools run in $img with no network"
  CHECKED+=("$img")
}

gb() { awk -v b="$1" 'BEGIN {printf "%.1f", b / 1073741824}'; }

# Free bytes where the runtime stores images.
storage_free_bytes() {
  local root=""
  case "$(basename "$RT")" in
    docker) root="$("$RT" info -f '{{.DockerRootDir}}' 2>/dev/null)" || root="" ;;
    podman) root="$("$RT" info -f '{{.Store.GraphRoot}}' 2>/dev/null)" || root="" ;;
  esac
  [[ -n "$root" && -d "$root" ]] || root=/
  df -PB1 "$root" | awk 'NR==2 {print $4}'
}

# Compressed size of IMAGE for linux/$ARCH from the registry, or empty.
image_size_bytes() {
  "$RT" manifest inspect -v "$1" 2>/dev/null | python3 -c '
import json, sys
want = sys.argv[1]
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for e in (d if isinstance(d, list) else [d]):
    plat = (e.get("Descriptor") or {}).get("platform") or {}
    if plat and plat.get("architecture") != want:
        continue
    m = e.get("OCIManifest") or e.get("SchemaV2Manifest") or {}
    layers = m.get("layers") or []
    if layers:
        print(sum(int(l.get("size", 0)) for l in layers))
        break
' "$ARCH" 2>/dev/null || true
}

# 1. stock image
STOCK="$OS_ID:$OS_VER"
"$RT" pull --platform "linux/$ARCH" "$STOCK" >/dev/null || die "cannot pull $STOCK"
run_check stock "$STOCK"

# 2. oldest package set of this release (Ubuntu: release pocket only).
if [[ "$OS_ID" == ubuntu ]]; then
  OLDEST="goliath-sysdeps-oldest:$TARGET"
  name="goliath-sysdeps-oldest-$$"
  info "[oldest] building $OLDEST: $STOCK with every package moved to the release pocket"
  "$RT" run --name "$name" --platform "linux/$ARCH" --user 0 --entrypoint bash "$STOCK" -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
codename="$(. /etc/os-release && printf %s "$VERSION_CODENAME")"
printf "Package: *\nPin: release a=%s\nPin-Priority: 1001\n" "$codename" >/etc/apt/preferences.d/goliath-oldest
{ apt-get -q update && apt-get -q -y --allow-downgrades -o Dpkg::Options::=--force-confold dist-upgrade; } >/tmp/prep.log 2>&1 \
  || { tail -n 30 /tmp/prep.log; exit 1; }
rm -f /etc/apt/preferences.d/goliath-oldest
apt-get clean; rm -rf /var/lib/apt/lists/*
dpkg-query -W libc6 libtinfo6' >"$WORK/oldest.log" 2>&1 || { cat "$WORK/oldest.log" >&2; "$RT" rm -f "$name" >/dev/null 2>&1 || true; die "could not build the oldest-$OS_VER image"; }
  "$RT" commit "$name" "$OLDEST" >/dev/null || { "$RT" rm -f "$name" >/dev/null 2>&1 || true; die "could not commit $OLDEST"; }
  "$RT" rm -f "$name" >/dev/null 2>&1 || true
  PULLED+=("$OLDEST")
  sed 's/^/[check-system-deps] [oldest]   base: /' "$WORK/oldest.log" >&2
  run_check oldest "$OLDEST"
fi

# 3. worker images (full offline bundle install; no tools-only shortcut).
# GHCR goliath-worker may be private / not yet published at release time: try
# pull, then fall back to the Clara NGC base (same Ubuntu) when NGC_API_KEY is
# set; otherwise skip that leg with a loud warning (stock+oldest still gate).
CLARA_FALLBACK="${GOLIATH_SYSDEPS_CLARA_FALLBACK:-nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1}"
for img in "${IMAGES[@]}"; do
  [[ -n "$img" ]] || continue
  check_img="$img"
  if ! "$RT" image inspect "$img" >/dev/null 2>&1; then
    free="$(storage_free_bytes)"
    size="$(image_size_bytes "$img")"
    if [[ -n "$size" ]]; then
      need=$(( size * 3 + 5 * 1024 * 1024 * 1024 ))
    else
      need=$(( MIN_FREE_GB * 1024 * 1024 * 1024 ))
    fi
    if [[ "$free" -lt "$need" ]]; then
      msg="not enough container storage to pull worker image $img (needs ~$(gb "$need") GB${size:+ for $(gb "$size") GB compressed}, $(gb "$free") GB free)"
      [[ "$REQUIRE_WORKER" != 1 ]] || die "$msg; --require-worker-images is set"
      warn "$msg; skipped. The stock and oldest-$OS_VER checks above still gate this release."
      continue
    fi
    info "[worker] pulling $img${size:+ ($(gb "$size") GB compressed)}; $(gb "$free") GB free"
    if ! "$RT" pull --platform "linux/$ARCH" "$img" >/dev/null 2>"$WORK/pull.err"; then
      # Private GHCR / unpublished tag: prove the jammy bundle on Clara base instead.
      if [[ "$img" == *goliath-worker* || "$img" == *clara-parabricks* ]]; then
        if [[ -n "${NGC_API_KEY:-}" ]]; then
          info "[worker] cannot pull $img; logging into nvcr.io and using Clara base $CLARA_FALLBACK for the offline check"
          echo "$NGC_API_KEY" | "$RT" login nvcr.io -u '$oauthtoken' --password-stdin >/dev/null
          "$RT" pull --platform "linux/$ARCH" "$CLARA_FALLBACK" >/dev/null \
            || die "cannot pull Clara fallback $CLARA_FALLBACK"
          PULLED+=("$CLARA_FALLBACK")
          check_img="$CLARA_FALLBACK"
        else
          msg="cannot pull worker image $img (see $WORK/pull.err); NGC_API_KEY unset so Clara fallback skipped"
          [[ "$REQUIRE_WORKER" != 1 ]] || die "$msg; --require-worker-images is set"
          warn "$msg; skipped worker-image leg. Stock and oldest-$OS_VER checks still gate this release."
          continue
        fi
      else
        die "cannot pull worker image $img"
      fi
    else
      PULLED+=("$img")
    fi
  fi
  run_check worker "$check_img"
done
info "the $TARGET bundle installs offline and every tool runs in: ${CHECKED[*]}"
