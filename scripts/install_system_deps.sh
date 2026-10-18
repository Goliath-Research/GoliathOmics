#!/usr/bin/env bash
# Install the external tools the worker pipeline needs (scripts/system_tools.list)
# on this host's local disk. Provider-neutral: runs on any Linux worker.
#
#   install_system_deps.sh            install whatever is missing, then verify
#   install_system_deps.sh --check    only report; exit 1 if anything is missing
#   install_system_deps.sh --dry-run  print the install command, change nothing
#   install_system_deps.sh --bundle DIR
#                    install from the release's offline bundle first:
#                    DIR/<os>-<version>-<arch>/ (*.deb, SHA256SUMS, bundle.json,
#                    built by bundle_system_deps.sh). The bundle is verified and
#                    copied to local disk before dpkg/apt touch it; nothing is
#                    executed from DIR (normally on /work). apt installs the
#                    local .deb files (absolute paths) with every configured
#                    source hidden, so the bundle path never touches the
#                    network. The online package manager is a last resort,
#                    with a loud warning, when no bundle matches this host or
#                    the bundle could not provide every tool.
#
# Idempotent: tools already on PATH are skipped and nothing runs when all are
# present. Uses apt-get (Debian/Ubuntu), else dnf, yum or apk. Needs root (or
# passwordless sudo). Packages go to the OS root filesystem (/usr), never the
# shared /work volume. apt locks are waited for and installs are retried.
# Exits non-zero, naming the tools, if anything is still missing afterwards:
# a missing tool must stop the worker, not silently drop pipeline steps.

set -euo pipefail

# Only OS tools run here (apt, dpkg, sha256sum). A caller's LD_LIBRARY_PATH
# can name private library dirs (MethylExtractor's bundled Ubuntu 22.04 libs:
# libcurl, libzstd, liblzma, libssl) that break the host's own binaries.
unset LD_LIBRARY_PATH LD_PRELOAD

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${GOLIATH_SYSTEM_TOOLS_MANIFEST:-$HERE/system_tools.list}"
RETRIES="${GOLIATH_SYSDEPS_RETRIES:-5}"
RETRY_SECS="${GOLIATH_SYSDEPS_RETRY_SECS:-10}"
LOCK_TIMEOUT="${GOLIATH_SYSDEPS_LOCK_TIMEOUT:-300}"
MODE=install
BUNDLE_ROOT="${GOLIATH_SYSTEM_DEPS_BUNDLE:-}"
LOCAL_STAGE_PARENT="${GOLIATH_SYSDEPS_STAGE_DIR:-/var/tmp}"

log() { printf '[install-system-deps] %s\n' "$*" >&2; }
die() { printf '[install-system-deps] ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check; shift ;;
    --dry-run) MODE=dry-run; shift ;;
    --bundle) [[ $# -ge 2 ]] || die "--bundle needs a value"; BUNDLE_ROOT="$2"; shift 2 ;;
    --manifest) [[ $# -ge 2 ]] || die "--manifest needs a value"; MANIFEST="$2"; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -f "$MANIFEST" ]] || die "tool manifest not found: $MANIFEST"
[[ "$RETRIES" =~ ^[1-9][0-9]*$ ]] || die "GOLIATH_SYSDEPS_RETRIES must be a positive integer"

# Parse the manifest: BIN[i] APT[i] DNF[i] APK[i].
BIN=() APT=() DNF=() APK=()
while read -r line; do
  line="${line%%#*}"
  read -r b a d k _ <<<"$line" || true
  [[ -n "${b:-}" ]] || continue
  [[ -n "${a:-}" && -n "${d:-}" && -n "${k:-}" ]] || die "malformed manifest line in $MANIFEST: '$line'"
  BIN+=("$b") APT+=("$a") DNF+=("$d") APK+=("$k")
done <"$MANIFEST"
[[ "${#BIN[@]}" -gt 0 ]] || die "tool manifest $MANIFEST lists no tools"

missing_idx() {
  local i
  for i in "${!BIN[@]}"; do
    command -v "${BIN[$i]}" >/dev/null 2>&1 || printf '%s\n' "$i"
  done
}
names_of() { local i out=""; for i in "$@"; do out+="${out:+ }${BIN[$i]}"; done; printf '%s' "$out"; }

mapfile -t MISSING < <(missing_idx)
if [[ "${#MISSING[@]}" -eq 0 ]]; then
  log "all ${#BIN[@]} required tools present"
  exit 0
fi
log "missing: $(names_of "${MISSING[@]}")"
[[ "$MODE" != check ]] || exit 1

# ---- choose the package manager ----
PM=""
for c in apt-get dnf yum apk; do
  if command -v "$c" >/dev/null 2>&1; then PM="$c"; break; fi
done
[[ -n "$PM" ]] || die "no supported package manager (apt-get, dnf, yum, apk); install manually: $(names_of "${MISSING[@]}")"

SUDO=()
if [[ "$(id -u)" != 0 ]]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    SUDO=(sudo -n)
  else
    die "not root and no passwordless sudo; cannot install: $(names_of "${MISSING[@]}")"
  fi
fi

# ---- offline bundle (release artifacts) ----
host_target() {
  local id="" ver="" arch=""
  [[ -r "${GOLIATH_OS_RELEASE:-/etc/os-release}" ]] || return 1
  # shellcheck disable=SC1090
  id="$(. "${GOLIATH_OS_RELEASE:-/etc/os-release}" && printf '%s' "${ID:-}")"
  # shellcheck disable=SC1090
  ver="$(. "${GOLIATH_OS_RELEASE:-/etc/os-release}" && printf '%s' "${VERSION_ID:-}")"
  arch="$(dpkg --print-architecture 2>/dev/null)" || return 1
  [[ -n "$id" && -n "$ver" && -n "$arch" ]] || return 1
  printf '%s-%s-%s' "$id" "$ver" "$arch"
}

deb_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'
}

# True when the host should take DEB: the package is not installed, or the
# installed version is older than the bundled one. The bundle is the
# dependency closure resolved on an up-to-date image of this OS release, so
# its packages can need the updated version of a library the host already
# has (libncurses6 needs the exact libtinfo6 it was built with). Never
# downgrade or reinstall.
deb_wanted() {
  local deb="$1" pkg ver have
  pkg="$(dpkg-deb -f "$deb" Package 2>/dev/null)" || return 1
  [[ -n "$pkg" ]] || return 1
  deb_installed "$pkg" || return 0
  ver="$(dpkg-deb -f "$deb" Version 2>/dev/null)" || return 1
  have="$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null)" || return 0
  [[ -n "$ver" && -n "$have" ]] || return 1
  dpkg --compare-versions "$have" lt "$ver"
}

# install_from_bundle: 0 when the bundle ran (tools re-checked afterwards).
install_from_bundle() {
  local target src stage d
  local -a todo=()
  [[ -n "$BUNDLE_ROOT" ]] || return 1
  [[ "$PM" == apt-get ]] || { log "bundle: $PM host; bundles are .deb only"; return 1; }
  target="$(host_target)" || { log "bundle: cannot identify this OS; skipping the bundle"; return 1; }
  src="$BUNDLE_ROOT/$target"
  if [[ ! -f "$src/SHA256SUMS" ]]; then
    log "bundle: none for $target under $BUNDLE_ROOT; falling back to $PM online"
    return 1
  fi
  if [[ "$MODE" == dry-run ]]; then
    log "dry run: install bundled .deb files from $src"
    exit 0
  fi
  mkdir -p "$LOCAL_STAGE_PARENT"
  stage="$(mktemp -d "$LOCAL_STAGE_PARENT/goliath-sysdeps.XXXXXX")"
  # Copy to local disk and verify there, so what dpkg installs is exactly
  # what was checked, and nothing executes from the shared volume.
  cp -- "$src/SHA256SUMS" "$stage/" || { rm -rf "$stage"; return 1; }
  shopt -s nullglob
  for d in "$src"/*.deb; do cp -- "$d" "$stage/" || { rm -rf "$stage"; return 1; }; done
  shopt -u nullglob
  if ! (cd "$stage" && sha256sum --check --quiet --strict SHA256SUMS >&2); then
    log "bundle: checksum mismatch in $src; ignoring it"
    rm -rf "$stage"
    return 1
  fi
  shopt -s nullglob
  for d in "$stage"/*.deb; do
    deb_wanted "$d" && todo+=("$d")
  done
  shopt -u nullglob
  if [[ "${#todo[@]}" -eq 0 ]]; then
    log "bundle: every bundled package is already installed"
    rm -rf "$stage"
    return 0
  fi
  log "installing ${#todo[@]} bundled packages for $target (offline)"
  # apt resolves dependencies among the given files and the installed
  # packages only: an empty source list hides every configured repository,
  # so nothing is downloaded (no apt-get update either). The paths are
  # absolute ($stage is). Do not add --no-download: with local .deb
  # arguments apt 2.x then looks the files up in its archive cache by bare
  # name and aborts with "Internal Error, Pathname to install is not
  # absolute 'pigz_2.8-1_amd64.deb'".
  mkdir -p "$stage/sources.list.d"
  : >"$stage/sources.list"
  APT_LOG="$(mktemp)"
  if ! retry apt_quiet install --no-install-recommends \
      -o "Dir::Etc::SourceList=$stage/sources.list" \
      -o "Dir::Etc::SourceParts=$stage/sources.list.d" \
      "${todo[@]}"; then
    log "bundle: offline install failed"
    rm -rf "$stage" "$APT_LOG"
    return 1
  fi
  rm -rf "$stage" "$APT_LOG"
  return 0
}

# The online package manager is the last resort; say so loudly.
ONLINE_WARNED=0
warn_online() {
  ONLINE_WARNED=1
  printf '[install-system-deps] WARNING: %s\n' "$*" >&2
  printf '[install-system-deps] WARNING: installing from the network with %s; the release bundle should make this unnecessary\n' "$PM" >&2
}

PKGS=()
UNPACKAGED=()
for i in "${MISSING[@]}"; do
  case "$PM" in
    apt-get) p="${APT[$i]}" ;;
    dnf|yum) p="${DNF[$i]}" ;;
    apk) p="${APK[$i]}" ;;
  esac
  if [[ "$p" == - ]]; then
    UNPACKAGED+=("${BIN[$i]}")
  elif [[ " ${PKGS[*]} " != *" $p "* ]]; then
    PKGS+=("$p")
  fi
done

retry() {
  local n=1
  until "$@"; do
    [[ "$n" -lt "$RETRIES" ]] || return 1
    log "attempt $n failed (package manager busy or mirror error); retrying in ${RETRY_SECS}s"
    sleep "$RETRY_SECS"
    n=$((n + 1))
  done
}

apt_quiet() {
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -q -y \
    -o "DPkg::Lock::Timeout=$LOCK_TIMEOUT" -o Dpkg::Use-Pty=0 "$@" >"$APT_LOG" 2>&1 \
    || { tail -n 20 "$APT_LOG" >&2; return 1; }
}

# Keep package-manager scratch on local disk, never the shared volume.
TMPDIR=/tmp
export TMPDIR
APT_LOG=""
trap 'rm -f "${APT_LOG:-}"' EXIT

if install_from_bundle; then
  hash -r
  mapfile -t MISSING < <(missing_idx)
  if [[ "${#MISSING[@]}" -eq 0 ]]; then
    log "installed from the release bundle; all ${#BIN[@]} required tools present"
    exit 0
  fi
  warn_online "still missing after the release bundle: $(names_of "${MISSING[@]}")"
  PKGS=()
  UNPACKAGED=()
  for i in "${MISSING[@]}"; do
    case "$PM" in
      apt-get) p="${APT[$i]}" ;;
      dnf|yum) p="${DNF[$i]}" ;;
      apk) p="${APK[$i]}" ;;
    esac
    if [[ "$p" == - ]]; then
      UNPACKAGED+=("${BIN[$i]}")
    elif [[ " ${PKGS[*]} " != *" $p "* ]]; then
      PKGS+=("$p")
    fi
  done
fi

if [[ "${#PKGS[@]}" -gt 0 ]]; then
  if [[ "$MODE" == dry-run ]]; then
    log "dry run: $PM install ${PKGS[*]}"
    exit 0
  fi
  if [[ -n "$BUNDLE_ROOT" && "$ONLINE_WARNED" == 0 ]]; then
    warn_online "the release bundle under $BUNDLE_ROOT did not install: ${PKGS[*]}"
  fi
  log "installing with $PM: ${PKGS[*]}"
  APT_LOG="$(mktemp)"
  case "$PM" in
    apt-get)
      retry apt_quiet update || die "apt-get update failed after $RETRIES attempts"
      retry apt_quiet install --no-install-recommends "${PKGS[@]}" \
        || die "apt-get install ${PKGS[*]} failed after $RETRIES attempts"
      ;;
    dnf|yum)
      retry "${SUDO[@]}" "$PM" -q -y install "${PKGS[@]}" >&2 \
        || die "$PM install ${PKGS[*]} failed after $RETRIES attempts (bedtools/fastp may need EPEL)"
      ;;
    apk)
      retry "${SUDO[@]}" apk add --no-cache -q "${PKGS[@]}" >&2 \
        || die "apk add ${PKGS[*]} failed after $RETRIES attempts"
      ;;
  esac
fi

hash -r
mapfile -t STILL < <(missing_idx)
if [[ "${#STILL[@]}" -gt 0 ]]; then
  [[ "${#UNPACKAGED[@]}" -eq 0 ]] || log "no $PM package known for: ${UNPACKAGED[*]}"
  die "required tools still missing after install: $(names_of "${STILL[@]}"); the worker cannot run every pipeline step without them"
fi
log "installed; all ${#BIN[@]} required tools present"
