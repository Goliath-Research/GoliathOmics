#!/usr/bin/env bash
# Release-time: bundle the worker system tools (scripts/system_tools.list) as
# an offline apt repository for one target OS/arch.
#
#   bundle_system_deps.sh --target ubuntu-24.04-amd64 --output DIR
#
# Writes DIR/<target>/{*.deb,Packages,Release,SHA256SUMS,bundle.json}.
#
# The package set is the COMPLETE dependency closure of the manifest's apt
# packages, resolved against an EMPTY dpkg status (as if nothing were
# installed) from the release, -updates and -security pockets of the target
# (for Ubuntu 24.04: noble, noble-updates, noble-security). Nothing is left
# out because the build image happens to have it: a pod image can carry an
# older (or no) copy of any of those packages, and every exact-version
# dependency inside the set (libncurses6 = libtinfo6 of the same build) is
# satisfied by the set itself. The build image's own installed packages and
# apt sources do not matter; only its apt and archive keyring are used.
# A from-scratch `apt-get --simulate` against the finished bundle (no
# sources, empty status) proves the closure is self-contained before the
# bundle is written.
#
# Packages + Release make DIR/<target> a flat apt repository. Pods install
# with install_system_deps.sh --bundle: apt resolves the missing tools from
# that repository only (no network), upgrading host packages to the bundled
# versions only where a dependency requires it.
#
# Why .deb: GoliathOmics workers run Ubuntu 24.04 on every provider today.
# Distro packages are small, get security fixes from the distro, and install
# with the same apt the online fallback uses. Another OS is one more --target
# (with its own image); a host whose OS has no bundle falls back to its online
# package manager. check_system_deps_bundle.sh proves the bundle installs
# offline in clean and worker images before a release ships.
#
# Needs docker or podman (GOLIATH_CONTAINER_RUNTIME). An arch other than the
# runner's needs qemu binfmt (docker/setup-qemu-action). Mirrors can be
# overridden with GOLIATH_BUNDLE_APT_MIRROR / GOLIATH_BUNDLE_APT_SECURITY_MIRROR.
# For tests, GOLIATH_BUNDLE_IN_HOST=1 runs the resolver on this host instead
# (it must be the target OS, or GOLIATH_OS_RELEASE must say so; it only
# writes to private temp dirs, never to the host's apt state).

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
    -h|--help) sed -n '2,39p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# Runs inside the target image (or this host in tests). Everything apt
# touches lives in a private work dir: its own sources (release, -updates,
# -security), lists, cache and an EMPTY dpkg status, so the closure is the
# full dependency set of $PKGS regardless of what the image has installed.
# Writes the debs, Packages, Release and versions.txt to $OUT_DIR.
RESOLVER='set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
arch="$(dpkg --print-architecture)"
[[ "$arch" == "$WANT_ARCH" ]] || { echo "image arch $arch != $WANT_ARCH" >&2; exit 1; }
osr="${OS_RELEASE:-/etc/os-release}"
id="$(. "$osr" && printf %s "${ID:-}")"
ver="$(. "$osr" && printf %s "${VERSION_ID:-}")"
codename="$(. "$osr" && printf %s "${VERSION_CODENAME:-}")"
[[ "$id" == "$WANT_OS" && "$ver" == "$WANT_VER" ]] || { echo "image is $id $ver, not $WANT_OS $WANT_VER" >&2; exit 1; }
[[ -n "$codename" ]] || { echo "no VERSION_CODENAME in $osr" >&2; exit 1; }
case "$id" in
  ubuntu)
    case "$arch" in amd64|i386) m="http://archive.ubuntu.com/ubuntu"; s="http://security.ubuntu.com/ubuntu" ;;
                    *) m="http://ports.ubuntu.com/ubuntu-ports"; s="$m" ;; esac
    comps="main universe restricted multiverse"; key=/usr/share/keyrings/ubuntu-archive-keyring.gpg ;;
  debian)
    m="http://deb.debian.org/debian"; s="http://deb.debian.org/debian-security"
    comps="main"; key=/usr/share/keyrings/debian-archive-keyring.gpg ;;
esac
m="${APT_MIRROR:-$m}"; s="${APT_SECURITY_MIRROR:-$s}"
W="$(mktemp -d)"
trap "rm -rf \"$W\"" EXIT
mkdir -p "$W/lists/partial" "$W/cache" "$W/parts" "$W/prefs" "$W/archives/partial" "$W/none"
: >"$W/status"; : >"$W/sources.list"
printf "Types: deb\nURIs: %s\nSuites: %s %s-updates\nComponents: %s\nSigned-By: %s\n\nTypes: deb\nURIs: %s\nSuites: %s-security\nComponents: %s\nSigned-By: %s\n" \
  "$m" "$codename" "$codename" "$comps" "$key" "$s" "$codename" "$comps" "$key" >"$W/parts/goliath.sources"
A=(-o "Dir::Etc::SourceList=$W/sources.list" -o "Dir::Etc::SourceParts=$W/parts"
   -o Dir::Etc::Preferences=/dev/null -o "Dir::Etc::PreferencesParts=$W/prefs"
   -o "Dir::State::Lists=$W/lists" -o "Dir::State::status=$W/status"
   -o "Dir::State::extended_states=$W/extended_states" -o "Dir::Cache=$W/cache"
   -o "Dir::Cache::archives=$W/archives" -o APT::Sandbox::User=root -o Debug::NoLocking=1
   -o APT::Install-Recommends=false -o APT::Install-Suggests=false
   -o Acquire::Languages=none -o Dpkg::Use-Pty=0)
apt-get -q "${A[@]}" -o APT::Update::Error-Mode=any update >"$W/update.log" 2>&1 || { cat "$W/update.log" >&2; exit 1; }
for suite in "$codename" "$codename-updates" "$codename-security"; do
  compgen -G "$W/lists/*_dists_${suite}_*Packages*" >/dev/null || { echo "apt has no package lists for $suite" >&2; exit 1; }
done
apt-get -q -y "${A[@]}" --download-only install $PKGS >"$W/download.log" 2>&1 || { cat "$W/download.log" >&2; exit 1; }
shopt -s nullglob
debs=("$W/archives"/*.deb)
[[ "${#debs[@]}" -gt 0 ]] || { echo "apt downloaded nothing" >&2; exit 1; }
mv -- "${debs[@]}" "$OUT_DIR/"
# Flat apt repository: Packages (control stanza + Filename/Size/SHA256 per
# deb) and a Release that pins its checksum; pods add it as
# "deb [trusted=yes] file:DIR ./" with SHA256SUMS as the integrity root.
cd "$OUT_DIR"
: >Packages; : >versions.txt
for d in *.deb; do
  dpkg-deb -f "$d" >>Packages
  printf "Filename: ./%s\nSize: %s\nSHA256: %s\n\n" "$d" "$(stat -c %s "$d")" "$(sha256sum "$d" | cut -d" " -f1)" >>Packages
  printf "%s %s %s\n" "$(dpkg-deb -f "$d" Package)" "$(dpkg-deb -f "$d" Version)" "$(dpkg-deb -f "$d" Architecture)" >>versions.txt
done
printf "Origin: GoliathOmics\nLabel: goliath-system-deps\nSuite: %s\nArchitectures: %s all\nDate: %s\nSHA256:\n %s %s Packages\n" \
  "$codename" "$arch" "$(LC_ALL=C date -u "+%a, %d %b %Y %H:%M:%S UTC")" \
  "$(sha256sum Packages | cut -d" " -f1)" "$(stat -c %s Packages)" >Release
# Self-containment proof: from an empty system, with ONLY this bundle as a
# source, apt can install every manifest package.
printf "deb [trusted=yes] file:%s ./\n" "$OUT_DIR" >"$W/bundle.list"
rm -rf "$W/lists" "$W/cache"; mkdir -p "$W/lists/partial" "$W/cache"
B=("${A[@]}" -o "Dir::Etc::SourceList=$W/bundle.list" -o "Dir::Etc::SourceParts=$W/none")
apt-get -q "${B[@]}" update >"$W/update2.log" 2>&1 || { cat "$W/update2.log" >&2; exit 1; }
apt-get -q -s "${B[@]}" install $PKGS >"$W/sim.log" 2>&1 \
  || { cat "$W/sim.log" >&2; echo "the bundle is not self-contained" >&2; exit 1; }'

if [[ "${GOLIATH_BUNDLE_IN_HOST:-}" == 1 ]]; then
  info "resolving the full closure of ${PKGS[*]} on this host for $TARGET"
  WANT_ARCH="$ARCH" WANT_OS="$OS_ID" WANT_VER="$OS_VER" OUT_DIR="$DEST" PKGS="${PKGS[*]}" \
    APT_MIRROR="${GOLIATH_BUNDLE_APT_MIRROR:-}" APT_SECURITY_MIRROR="${GOLIATH_BUNDLE_APT_SECURITY_MIRROR:-}" \
    OS_RELEASE="${GOLIATH_OS_RELEASE:-/etc/os-release}" \
    bash -c "$RESOLVER" || die "package resolution failed for $TARGET"
else
  RT="${GOLIATH_CONTAINER_RUNTIME:-}"
  if [[ -z "$RT" ]]; then
    for c in docker podman; do command -v "$c" >/dev/null 2>&1 && { RT="$c"; break; }; done
  fi
  [[ -n "$RT" ]] || die "docker or podman is required to bundle system deps for $TARGET"
  info "resolving the full closure of ${PKGS[*]} in $IMAGE (linux/$ARCH) with $RT"
  "$RT" pull --platform "linux/$ARCH" "$IMAGE" >/dev/null || die "cannot pull $IMAGE"
  "$RT" run --rm --platform "linux/$ARCH" \
    -e WANT_ARCH="$ARCH" -e WANT_OS="$OS_ID" -e WANT_VER="$OS_VER" -e OUT_DIR=/out -e PKGS="${PKGS[*]}" \
    -e APT_MIRROR="${GOLIATH_BUNDLE_APT_MIRROR:-}" -e APT_SECURITY_MIRROR="${GOLIATH_BUNDLE_APT_SECURITY_MIRROR:-}" \
    -v "$DEST:/out" "$IMAGE" bash -c "$RESOLVER; chown -R $(id -u):$(id -g) /out" \
    || die "package resolution failed for $TARGET in $IMAGE"
fi

shopt -s nullglob
debs=("$DEST"/*.deb)
[[ "${#debs[@]}" -gt 0 && -s "$DEST/Packages" && -s "$DEST/Release" && -s "$DEST/versions.txt" ]] \
  || die "resolver left no bundle in $DEST"
# Exactly one version per package, and every manifest apt package bundled.
dups="$(awk '{print $1}' "$DEST/versions.txt" | sort | uniq -d)"
[[ -z "$dups" ]] || die "more than one version bundled for: $dups"
for p in "${PKGS[@]}"; do
  awk -v p="$p" '$1==p {f=1} END {exit !f}' "$DEST/versions.txt" || die "package $p is missing from the bundle"
done
(cd "$DEST" && sha256sum -- *.deb Packages Release >SHA256SUMS)
bytes="$(du -sb "$DEST" | cut -f1)"
python3 - "$DEST/bundle.json" "$DEST/versions.txt" "$TARGET" "$OS_ID" "$OS_VER" "$ARCH" "$IMAGE" "$bytes" \
  "${#debs[@]}" "$(printf '%s,' "${PKGS[@]}")" "$(printf '%s,' "${TOOLS[@]}")" <<'PY'
import json, sys
out, versions, target, os_id, os_ver, arch, image, size, n, pkgs, tools = sys.argv[1:]
sp = lambda s: [x for x in s.split(",") if x]
debs = {}
for line in open(versions):
    f = line.split()
    if len(f) >= 2:
        debs[f[0]] = f[1]
json.dump({"format": "deb-repo", "target": target, "os_id": os_id, "os_version": os_ver, "arch": arch,
           "image": image, "closure": "full (empty dpkg status; release+updates+security)",
           "packages": sp(pkgs), "tools": sp(tools), "debs": dict(sorted(debs.items())),
           "deb_count": int(n), "bytes": int(size)}, open(out, "w"), indent=2)
PY
rm -f "$DEST/versions.txt"
info "bundled the full closure: ${#debs[@]} .deb files for $TARGET ($(du -sh "$DEST" | cut -f1))"
