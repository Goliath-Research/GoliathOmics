#!/usr/bin/env bash
# Install the external tools the worker pipeline needs (scripts/system_tools.list)
# on this host's local disk. Provider-neutral: runs on any Linux worker.
#
#   install_system_deps.sh            install whatever is missing, then verify
#   install_system_deps.sh --check    only report; exit 1 if anything is missing
#   install_system_deps.sh --dry-run  print the install command, change nothing
#   install_system_deps.sh --bundle DIR
#                    install from the release's offline bundle first:
#                    DIR/<os>-<version>-<arch>/ (*.deb, Packages, Release,
#                    SHA256SUMS, bundle.json,
#                    built by bundle_system_deps.sh). The bundle is verified and
#                    copied to local disk before dpkg/apt touch it; nothing is
#                    executed from DIR (normally on /work). The bundle is a
#                    flat apt repository holding the full dependency closure;
#                    apt installs the missing tools from it in one
#                    transaction, with every configured source, list, pin and
#                    archive cache hidden, so it never touches the network.
#                    Host packages move to the bundled version only where a
#                    dependency needs it; nothing is removed. The online
#                    package manager is a last resort,
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
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# Apt options that make the bundle the ONLY package source: a private source
# list naming the bundle as a flat file: repository, private lists, caches
# and archives (so nothing from /var/cache/apt/archives or a stale
# `apt-get update` is used) and no host pinning. The host's dpkg status,
# holds and auto-installed marks are used as they are.
bundle_apt_opts() {  # bundle_apt_opts STAGE
  local st="$1"
  BOPTS=(-o "Dir::Etc::SourceList=$st/bundle.list" -o "Dir::Etc::SourceParts=$st/none"
         -o Dir::Etc::Preferences=/dev/null -o "Dir::Etc::PreferencesParts=$st/none"
         -o "Dir::State::Lists=$st/lists" -o "Dir::Cache=$st/cache"
         -o "Dir::Cache::archives=$st/archives" -o APT::Sandbox::User=root
         -o Acquire::Languages=none -o APT::Install-Recommends=false -o APT::Install-Suggests=false)
}

# apt_sim OUT ARGS...: `apt-get --simulate` (no lock, no changes) into OUT.
apt_sim() {
  local out="$1"; shift
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get -q -s -o Dpkg::Use-Pty=0 "$@" >"$out" 2>&1
}

# bundled_version PKG: the version the bundle carries (from its Packages).
bundled_version() {
  awk -v p="$1" '/^Package: /{cur=$2} /^Version: /{if (cur==p) {print $2; exit}}' "$STAGE_REPO/Packages"
}

installed_version() {
  dpkg-query -W -f='${Status} ${Version}' "$1" 2>/dev/null | awk '$3=="installed" {print $4}'
}

# install_from_bundle: 0 when the bundle ran (tools re-checked afterwards).
#
# apt installs the missing tools' packages from the bundle repository in one
# transaction. Host packages are upgraded to the bundled version only when a
# dependency needs it (libncurses6 needs the exact libtinfo6 it was built
# with). A host package NEWER than the bundled one is kept unless a bundled
# package has an exact-version dependency on the bundled version; then only
# those packages are moved to the bundled version, with a loud warning, and
# only if apt can do it without removing anything or breaking any installed
# package's dependencies. Otherwise the conflict is reported and the online
# fallback runs.
install_from_bundle() {
  local target src stage d i p sim extra_new line x y v have cand bv hv n_new n_up n_down round
  local -a want=() extra=() dg=()
  [[ -n "$BUNDLE_ROOT" ]] || return 1
  [[ "$PM" == apt-get ]] || { log "bundle: $PM host; bundles are .deb only"; return 1; }
  target="$(host_target)" || { log "bundle: cannot identify this OS; skipping the bundle"; return 1; }
  src="$BUNDLE_ROOT/$target"
  if [[ ! -f "$src/SHA256SUMS" ]]; then
    log "bundle: none for $target under $BUNDLE_ROOT; falling back to $PM online"
    return 1
  fi
  if [[ ! -f "$src/Packages" || ! -f "$src/Release" ]]; then
    log "bundle: $src has no apt repository index (Packages/Release); ignoring it"
    return 1
  fi
  for i in "${MISSING[@]}"; do
    p="${APT[$i]}"
    [[ "$p" != - && " ${want[*]} " != *" $p "* ]] && want+=("$p")
  done
  [[ "${#want[@]}" -gt 0 ]] || { log "bundle: no apt package provides $(names_of "${MISSING[@]}")"; return 1; }
  if [[ "$MODE" == dry-run ]]; then
    log "dry run: install ${want[*]} offline from the bundled repository $src"
    exit 0
  fi
  mkdir -p "$LOCAL_STAGE_PARENT"
  stage="$(mktemp -d "$LOCAL_STAGE_PARENT/goliath-sysdeps.XXXXXX")"
  STAGE_REPO="$stage/repo"
  mkdir -p "$STAGE_REPO" "$stage/none" "$stage/lists/partial" "$stage/cache" "$stage/archives/partial"
  # Copy to local disk and verify there, so what apt installs is exactly
  # what was checked, and nothing executes from the shared volume.
  cp -- "$src/SHA256SUMS" "$src/Packages" "$src/Release" "$STAGE_REPO/" || { rm -rf "$stage"; return 1; }
  shopt -s nullglob
  for d in "$src"/*.deb; do cp -- "$d" "$STAGE_REPO/" || { rm -rf "$stage"; return 1; }; done
  shopt -u nullglob
  if ! (cd "$STAGE_REPO" && sha256sum --check --quiet --strict SHA256SUMS >&2) \
      || ! grep -q ' Packages$' "$STAGE_REPO/SHA256SUMS" || ! grep -q ' Release$' "$STAGE_REPO/SHA256SUMS"; then
    log "bundle: checksum mismatch in $src; ignoring it"
    rm -rf "$stage"
    return 1
  fi
  # A package that is installed but whose binary is missing is not asked for:
  # `apt-get install` would upgrade it (and its exact-version siblings the
  # bundle may not carry); the online fallback reports or repairs it.
  local -a inbundle=() lack=() broken=()
  for p in "${want[@]}"; do
    if [[ -n "$(installed_version "$p")" ]]; then broken+=("$p")
    elif [[ -n "$(bundled_version "$p")" ]]; then inbundle+=("$p")
    else lack+=("$p"); fi
  done
  [[ "${#broken[@]}" -eq 0 ]] || log "bundle: ${broken[*]} installed but the tool is missing; not reinstalling from the bundle"
  [[ "${#lack[@]}" -eq 0 ]] || log "bundle lacks ${lack[*]}; the online fallback below handles them"
  if [[ "${#inbundle[@]}" -eq 0 ]]; then rm -rf "$stage"; return 1; fi
  want=("${inbundle[@]}")
  # The path is absolute ($stage is) and local; trusted=yes because
  # SHA256SUMS (checked above) is the bundle's integrity root.
  printf 'deb [trusted=yes] file:%s ./\n' "$STAGE_REPO" >"$stage/bundle.list"
  bundle_apt_opts "$stage"
  APT_LOG="$(mktemp)"
  sim="$stage/sim.log"
  if ! apt_quiet "${BOPTS[@]}" update; then
    log "bundle: apt cannot read the bundled repository"
    rm -rf "$stage" "$APT_LOG"
    return 1
  fi
  # Plan with --simulate first (no lock, no retries for a plan apt rejects).
  round=0
  while :; do
    if apt_sim "$sim" "${BOPTS[@]}" --no-remove ${extra[@]:+--allow-downgrades} install "${want[@]}" "${extra[@]}"; then
      break
    fi
    # An exact-version dependency apt cannot meet because the host has a
    # NEWER build than the bundle:
    #   libncurses6 : Depends: libtinfo6 (= 6.4-2.2) but 6.4-2.3 is installed
    # moves libtinfo6 to the bundled 6.4-2.2; and an installed sibling that
    # then breaks (libncursesw6 6.4-2.3 needs libtinfo6 = 6.4-2.3) follows
    # to its bundled version when the bundle has one.
    extra_new=0
    while IFS= read -r line; do
      [[ "$line" =~ ^[[:space:]]*([^ ]+)\ :\ (Pre-)?Depends:\ ([^ ]+)\ \(=\ ([^\)]+)\)\ but\ ([^ ]+)\ is ]] || continue
      x="${BASH_REMATCH[1]%%:*}" y="${BASH_REMATCH[3]%%:*}" v="${BASH_REMATCH[4]}" have="${BASH_REMATCH[5]}"
      for cand in "$y $v $have" "$x $(bundled_version "$x") $(installed_version "$x")"; do
        read -r p bv hv <<<"$cand"
        [[ -n "${bv:-}" && -n "${hv:-}" && "$(bundled_version "$p")" == "$bv" ]] || continue
        dpkg --compare-versions "$hv" gt "$bv" 2>/dev/null || continue
        [[ " ${extra[*]} " == *" $p=$bv "* ]] && continue
        extra+=("$p=$bv") dg+=("$p $hv -> $bv")
        extra_new=1
      done
    done <"$sim"
    round=$((round + 1))
    if [[ "$extra_new" == 0 || "$round" -gt 5 ]]; then
      log "bundle: CONFLICT: the bundled packages cannot be installed next to this host's packages without removing or breaking something:"
      grep -E 'Depends:|Conflicts:|Breaks:|^E:' "$sim" | sed 's/^/[install-system-deps]   /' >&2 || tail -n 20 "$sim" >&2
      if grep -q 'remove is disabled' "$sim" \
          && apt_sim "$sim" "${BOPTS[@]}" ${extra[@]:+--allow-downgrades} install "${want[@]}" "${extra[@]}"; then
        log "bundle:   apt would have to remove: $(awk '/^Remv /{printf "%s%s %s", sep, $2, $3; sep=", "}' "$sim")"
        [[ "${#dg[@]}" -eq 0 ]] || log "bundle:   after moving down: $(IFS=';'; printf '%s' "${dg[*]}")"
      fi
      log "bundle: this image's packages and the release bundle do not fit together; add the image to the release check (build_release.sh --system-deps-check-images) and rebuild; the online fallback below is the last resort"
      rm -rf "$stage" "$APT_LOG"
      return 1
    fi
  done
  n_new="$(grep -cE '^Inst [^ ]+ \(' "$sim" || true)"
  n_up="$(grep -cE '^Inst [^ ]+ \[' "$sim" || true)"
  n_down="${#dg[@]}"
  if [[ "$n_down" -gt 0 ]]; then
    printf '[install-system-deps] WARNING: moving %s host package(s) DOWN to the bundled version, which bundled packages require exactly (apt keeps every installed package satisfied; nothing is removed):\n' "$n_down" >&2
    for line in "${dg[@]}"; do printf '[install-system-deps] WARNING:   %s\n' "$line" >&2; done
  fi
  log "installing ${want[*]} from the bundle for $target (offline): $n_new new, $n_up changed to the bundled version"
  if ! retry apt_quiet "${BOPTS[@]}" --no-remove ${extra[@]:+--allow-downgrades} install "${want[@]}" "${extra[@]}"; then
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
