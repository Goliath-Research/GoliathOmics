#!/bin/bash
# Install MethylExtractor from a release tarball into a pod-local directory.
#
# Shared volumes (a Runpod network volume at /work, some NFS mounts) ignore
# exec bits, and native files extracted onto them can be listed but not read
# back. Release tarballs stay on the shared volume; every machine extracts
# the binary onto its own local disk.
#
# Location (first match wins):
#   METHYL_EXTRACTOR_HOME            explicit install dir
#   $GOLIATH_LOCAL_ROOT/methyl-extractor-<arch>   (default /opt/goliath)
# A location under GOLIATH_WORK_MOUNT (default /work), or one that cannot be
# created, falls back to /tmp/goliath/methyl-extractor-<arch>.
#
# Source this file for methyl_extractor_local_home / install_methyl_extractor_local,
# or run it:
#   methyl_extractor_local.sh --tarball PATH [--arch KEY] [--sha256 HEX] [--print-home]

_mel_info() { echo "[INFO] $*"; }
_mel_die() { echo "[ERROR] $*" >&2; return 1; }

# True when $1 is the work mount or below it.
_mel_on_work() {
  local path="$1" work="${GOLIATH_WORK_MOUNT:-/work}"
  work="${work%/}"
  [[ -n "$work" ]] || return 1
  case "$path" in
    "$work"|"$work"/*) return 0 ;;
  esac
  return 1
}

# Print the pod-local install directory for ARCH.
methyl_extractor_local_home() {
  local arch="$1" home parent
  if [[ -n "${METHYL_EXTRACTOR_HOME:-}" ]]; then
    home="${METHYL_EXTRACTOR_HOME%/}"
  else
    home="${GOLIATH_LOCAL_ROOT:-/opt/goliath}"
    home="${home%/}/methyl-extractor-${arch}"
  fi
  parent="$(dirname "$home")"
  if _mel_on_work "$home"; then
    echo "[WARN] ${home} is on the shared work mount; using /tmp instead" >&2
    home="/tmp/goliath/methyl-extractor-${arch}"
  elif ! mkdir -p "$parent" 2>/dev/null || [[ ! -w "$parent" ]]; then
    if [[ ! -w "$home" ]]; then
      echo "[WARN] cannot write ${parent}; using /tmp instead" >&2
      home="/tmp/goliath/methyl-extractor-${arch}"
    fi
  fi
  printf '%s\n' "$home"
}

# Fail when BIN cannot be executed (missing, no exec bit, unreadable, bad
# loader). --help need not succeed; only "cannot execute" codes are fatal.
_mel_check_exec() {
  local bin="$1" rc=0
  [[ -f "$bin" ]] || { _mel_die "missing ${bin}"; return 1; }
  [[ -r "$bin" ]] || { _mel_die "cannot read ${bin}"; return 1; }
  [[ -x "$bin" ]] || { _mel_die "${bin} is not executable"; return 1; }
  # Bundled libs (libhts.so.3, libdeflate) live in <home>/lib.
  LD_LIBRARY_PATH="$(dirname "$(dirname "$bin")")/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$bin" --help >/dev/null 2>&1 </dev/null || rc=$?
  if [[ "$rc" -eq 126 || "$rc" -eq 127 ]]; then
    _mel_die "${bin} does not run (exit ${rc})"
    return 1
  fi
  return 0
}

# install_methyl_extractor_local TARBALL ARCH [EXPECTED_SHA256]
# Prints nothing on stdout; sets METHYL_EXTRACTOR_HOME, METHYL_EXTRACTOR_BIN,
# HDF5_PLUGIN_PATH and LD_LIBRARY_PATH (prepends <home>/lib, where the
# tarball bundles libhts.so.3) in the caller (exported). Idempotent: skips extraction
# when the installed copy came from a tarball with the same sha256.
install_methyl_extractor_local() {
  local tarball="$1" arch="$2" expected="${3:-}"
  local home bin actual stamp stage
  [[ -f "$tarball" ]] || { _mel_die "MethylExtractor tarball not found: ${tarball}"; return 1; }
  home="$(methyl_extractor_local_home "$arch")" || return 1
  bin="$home/bin/MethylExtractor"
  stamp="$home/.tarball.sha256"
  actual="$(sha256sum "$tarball" | awk '{print $1}')" || { _mel_die "cannot read ${tarball}"; return 1; }
  if [[ -n "$expected" && "$actual" != "$expected" ]]; then
    _mel_die "checksum mismatch for ${tarball} (expected ${expected}, got ${actual})"
    return 1
  fi
  if [[ -f "$stamp" && "$(cat "$stamp" 2>/dev/null)" == "$actual" ]] && _mel_check_exec "$bin" 2>/dev/null; then
    _mel_info "MethylExtractor already installed at ${home} (sha256 ${actual:0:12})" >&2
  else
    _mel_info "Extracting MethylExtractor to local disk: ${home}" >&2
    mkdir -p "$(dirname "$home")" || { _mel_die "cannot create $(dirname "$home")"; return 1; }
    stage="$(mktemp -d "${home}.new.XXXXXX")" || return 1
    if ! tar -xzf "$tarball" -C "$stage"; then
      rm -rf "$stage"
      _mel_die "could not extract ${tarball} (corrupt or truncated archive?)"
      return 1
    fi
    if [[ ! -f "$stage/bin/MethylExtractor" ]]; then
      rm -rf "$stage"
      _mel_die "${tarball} has no bin/MethylExtractor"
      return 1
    fi
    chmod 0755 "$stage" "$stage/bin/MethylExtractor"
    if ! _mel_check_exec "$stage/bin/MethylExtractor"; then
      rm -rf "$stage"
      return 1
    fi
    printf '%s\n' "$actual" >"$stage/.tarball.sha256"
    rm -rf "$home"
    mv "$stage" "$home"
    _mel_check_exec "$bin" || return 1
  fi
  cat >"$home/env.sh" <<ENV
# Source this file: source "$home/env.sh"
export METHYL_EXTRACTOR_HOME="$home"
export METHYL_EXTRACTOR_BIN="$bin"
export HDF5_PLUGIN_PATH="$home/lib/hdf5_zstd_plugin"
export LD_LIBRARY_PATH="$home/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export PATH="$home/bin:\$PATH"
ENV
  export METHYL_EXTRACTOR_HOME="$home"
  export METHYL_EXTRACTOR_BIN="$bin"
  export HDF5_PLUGIN_PATH="$home/lib/hdf5_zstd_plugin"
  case ":${LD_LIBRARY_PATH:-}:" in
    *":$home/lib:"*) ;;
    *) export LD_LIBRARY_PATH="$home/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
  esac
  _mel_info "MethylExtractor: ${bin}" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  _tarball="" _arch="" _sha="" _print=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tarball) _tarball="${2:-}"; shift 2 ;;
      --arch) _arch="${2:-}"; shift 2 ;;
      --sha256) _sha="${2:-}"; shift 2 ;;
      --print-home) _print=1; shift ;;
      -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done
  if [[ -z "$_arch" ]]; then
    if [[ "$(basename "$_tarball")" =~ methyl-extractor-linux-(aarch64|amd64) ]]; then
      _arch="${BASH_REMATCH[1]}"
    else
      case "$(uname -m)" in aarch64|arm64) _arch=aarch64 ;; *) _arch=amd64 ;; esac
    fi
  fi
  if [[ "$_print" -eq 1 && -z "$_tarball" ]]; then
    methyl_extractor_local_home "$_arch"
    exit 0
  fi
  [[ -n "$_tarball" ]] || { echo "[ERROR] --tarball is required" >&2; exit 1; }
  install_methyl_extractor_local "$_tarball" "$_arch" "$_sha"
  if [[ "$_print" -eq 1 ]]; then
    printf '%s\n' "$METHYL_EXTRACTOR_HOME"
  fi
fi
