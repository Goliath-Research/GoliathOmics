#!/usr/bin/env bash
# Release-time: bundle the worker system tools (scripts/system_tools.list) as
# offline-installable .deb files for one target OS/arch.
#
#   bundle_system_deps.sh --target ubuntu-24.04-amd64 --output DIR
#
# Writes DIR/<target>/{*.deb,SHA256SUMS,bundle.json}. The package set is the
# exact apt dependency closure of the manifest's apt packages on top of a
# clean image of the target (default image ubuntu:24.04 for ubuntu-24.04-*),
# resolved by `apt-get install --download-only` inside that image, so the
# bundle installs on any machine running that OS release with no network.
# Packages the clean image already has are not bundled (bundle.json lists
# them as preinstalled). Pods install the bundle with install_system_deps.sh
# --bundle, copying it to local disk first; nothing runs from /work.
#
# Why .deb: GoliathOmics workers run Ubuntu 24.04 on every provider today.
# Distro packages are small (a few MB, against hundreds for a conda-pack env
# of the same tools), get security fixes from the distro, and install with the
# same dpkg/apt the online fallback uses. Another OS is one more --target
# (with its own image); a host whose OS has no bundle falls back to its online
# package manager.
#
# Needs docker or podman (GOLIATH_CONTAINER_RUNTIME). An arch other than the
# runner's needs qemu binfmt (docker/setup-qemu-action). For tests,
# GOLIATH_BUNDLE_IN_HOST=1 runs the resolver on this host instead (it must be
# the target OS, and apt-get must work).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${GOLIATH_SYSTEM_TOOLS_MANIFEST:-$HERE/system_tools.list}"
TARGET="" OUTPUT="" IMAGE=""

die() { printf '[bundle-system-deps] ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '[bundle-system-deps] %s\n' "$*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --image) IMAGE="${2:-}"; shift 2 ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
[[ -n "$TARGET" && -n "$OUTPUT" ]] || die "--target and --output are required"
[[ "$TARGET" =~ ^([a-z]+)-([0-9.]+)-([a-z0-9_]+)$ ]] || die "--target must look like ubuntu-24.04-amd64 (got '$TARGET')"
OS_ID="${BASH_REMATCH[1]}" OS_VER="${BASH_REMATCH[2]}" ARCH="${BASH_REMATCH[3]}"
case "$OS_ID" in
  ubuntu|debian) ;;
  *) die "only Debian/Ubuntu (.deb) targets are supported (got $OS_ID)" ;;
esac
[[ -n "$IMAGE" ]] || IMAGE="$OS_ID:$OS_VER"
[[ -f "$MANIFEST" ]] || die "tool manifest not found: $MANIFEST"

mapfile -t PKGS < <(sed -e 's/#.*//' "$MANIFEST" | awk 'NF>=4 && $2!="-" {print $2}' | sort -u)
[[ "${#PKGS[@]}" -gt 0 ]] || die "no apt packages in $MANIFEST"
mapfile -t TOOLS < <(sed -e 's/#.*//' "$MANIFEST" | awk 'NF>=4 {print $1}')

DEST="$OUTPUT/$TARGET"
rm -rf "$DEST"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"

# Runs inside the target image (or this host in tests): resolve and download
# the closure into /out, list preinstalled packages.
RESOLVER='set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -qq -o Dpkg::Use-Pty=0 update >/dev/null
arch="$(dpkg --print-architecture)"
[[ "$arch" == "$WANT_ARCH" ]] || { echo "image arch $arch != $WANT_ARCH" >&2; exit 1; }
mkdir -p "$OUT_DIR/partial"
apt-get -qq -y --no-install-recommends --download-only \
  -o Dir::Cache::archives="$OUT_DIR" install $PKGS >/dev/null
rm -rf "$OUT_DIR/partial" "$OUT_DIR/lock"
: >"$OUT_DIR/preinstalled.txt"
for p in $PKGS; do
  if dpkg-query -W -f="\${Status}" "$p" 2>/dev/null | grep -q "install ok installed"; then
    echo "$p" >>"$OUT_DIR/preinstalled.txt"
  fi
done'

if [[ "${GOLIATH_BUNDLE_IN_HOST:-}" == 1 ]]; then
  info "resolving ${PKGS[*]} on this host for $TARGET"
  WANT_ARCH="$ARCH" OUT_DIR="$DEST" PKGS="${PKGS[*]}" bash -c "$RESOLVER" || die "package resolution failed for $TARGET"
else
  RT="${GOLIATH_CONTAINER_RUNTIME:-}"
  if [[ -z "$RT" ]]; then
    for c in docker podman; do command -v "$c" >/dev/null 2>&1 && { RT="$c"; break; }; done
  fi
  [[ -n "$RT" ]] || die "docker or podman is required to bundle system deps for $TARGET"
  info "resolving ${PKGS[*]} in $IMAGE (linux/$ARCH) with $RT"
  "$RT" run --rm --platform "linux/$ARCH" \
    -e WANT_ARCH="$ARCH" -e OUT_DIR=/out -e PKGS="${PKGS[*]}" \
    -v "$DEST:/out" "$IMAGE" bash -c "$RESOLVER; chown -R $(id -u):$(id -g) /out" \
    || die "package resolution failed for $TARGET in $IMAGE"
fi

shopt -s nullglob
debs=("$DEST"/*.deb)
mapfile -t PRE <"$DEST/preinstalled.txt"
rm -f "$DEST/preinstalled.txt"
# Every manifest apt package is either bundled or already in the clean image.
for p in "${PKGS[@]}"; do
  found=0
  for d in "${debs[@]}"; do [[ "$(basename "$d")" == "${p}_"* ]] && { found=1; break; }; done
  for q in "${PRE[@]}"; do [[ "$q" == "$p" ]] && found=1; done
  [[ "$found" == 1 ]] || die "package $p is neither bundled nor preinstalled in $IMAGE"
done
(cd "$DEST" && if [[ "${#debs[@]}" -gt 0 ]]; then sha256sum -- *.deb >SHA256SUMS; else : >SHA256SUMS; fi)
bytes="$(du -sb "$DEST" | cut -f1)"
python3 - "$DEST/bundle.json" "$TARGET" "$OS_ID" "$OS_VER" "$ARCH" "$IMAGE" "$bytes" \
  "${#debs[@]}" "$(printf '%s,' "${PKGS[@]}")" "$(printf '%s,' "${PRE[@]}")" "$(printf '%s,' "${TOOLS[@]}")" <<'PY'
import json, sys
out, target, os_id, os_ver, arch, image, size, n, pkgs, pre, tools = sys.argv[1:]
sp = lambda s: [x for x in s.split(",") if x]
json.dump({"format": "deb", "target": target, "os_id": os_id, "os_version": os_ver, "arch": arch,
           "image": image, "packages": sp(pkgs), "preinstalled": sp(pre), "tools": sp(tools),
           "deb_count": int(n), "bytes": int(size)}, open(out, "w"), indent=2)
PY
info "bundled ${#debs[@]} .deb files for $TARGET ($(du -sh "$DEST" | cut -f1)); preinstalled in image: ${PRE[*]:-none}"
