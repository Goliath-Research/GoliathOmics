#!/bin/bash
# Install GoliathExtractor from a release tarball into a pod-local directory.
#
# Shared volumes (a Runpod network volume at /work, some NFS mounts) ignore
# exec bits, and native files extracted onto them can be listed but not read
# back. Release tarballs stay on the shared volume; every machine extracts
# the binary onto its own local disk.
#
# Location (first match wins):
#   GOLIATH_EXTRACTOR_HOME / METHYL_EXTRACTOR_HOME   explicit install dir
#   $GOLIATH_LOCAL_ROOT/goliath-extractor-<arch>   (default /opt/goliath)
# A location under GOLIATH_WORK_MOUNT (default /work), or one that cannot be
# created, falls back to /tmp/goliath/goliath-extractor-<arch>.
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

_mel_home_override() {
  if [[ -n "${GOLIATH_EXTRACTOR_HOME:-}" ]]; then
    printf '%s\n' "${GOLIATH_EXTRACTOR_HOME%/}"
  elif [[ -n "${METHYL_EXTRACTOR_HOME:-}" ]]; then
    printf '%s\n' "${METHYL_EXTRACTOR_HOME%/}"
  fi
}

_mel_default_home_suffix() {
  printf 'goliath-extractor-%s' "$1"
}

# Resolve bin/goliath-extractor or legacy bin/MethylExtractor under $1.
_mel_bin_in_home() {
  local home="$1"
  if [[ -x "$home/bin/goliath-extractor" ]]; then
    printf '%s\n' "$home/bin/goliath-extractor"
  elif [[ -x "$home/bin/MethylExtractor" ]]; then
    printf '%s\n' "$home/bin/MethylExtractor"
  fi
}

# Tarball filename from manifest.json artifacts[arch] (goliath_extractor, else methyl_extractor).
manifest_extractor_tarball_name() {
  local manifest="$1" arch="$2"
  python3 - "$manifest" "$arch" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
arts = m.get("artifacts", {}).get(sys.argv[2], {})
print(arts.get("goliath_extractor") or arts.get("methyl_extractor") or "")
PY
}

# Print the pod-local install directory for ARCH.
methyl_extractor_local_home() {
  local arch="$1" home parent override
  override="$(_mel_home_override || true)"
  if [[ -n "$override" ]]; then
    home="$override"
  else
    home="${GOLIATH_LOCAL_ROOT:-/opt/goliath}"
    home="${home%/}/$(_mel_default_home_suffix "$arch")"
  fi
  parent="$(dirname "$home")"
  if _mel_on_work "$home"; then
    echo "[WARN] ${home} is on the shared work mount; using /tmp instead" >&2
    home="/tmp/goliath/$(_mel_default_home_suffix "$arch")"
  elif ! mkdir -p "$parent" 2>/dev/null || [[ ! -w "$parent" ]]; then
    if [[ ! -w "$home" ]]; then
      echo "[WARN] cannot write ${parent}; using /tmp instead" >&2
      home="/tmp/goliath/$(_mel_default_home_suffix "$arch")"
    fi
  fi
  printf '%s\n' "$home"
}

# Fail when BIN cannot be executed (missing, no exec bit, unreadable, bad
# loader). --help need not succeed; only "cannot execute" codes are fatal.
#
# The binary must run with no LD_LIBRARY_PATH at all. Release tarballs
# bundle every non-glibc library in <home>/lib (built on Ubuntu 22.04:
# libhts, libhdf5, libcurl, libssl, libzstd, ...) and resolve them through
# RUNPATH $ORIGIN/../lib. Putting <home>/lib on LD_LIBRARY_PATH instead makes
# every other program in that environment load those 22.04 libraries:
# /usr/bin/curl died with "undefined symbol: curl_global_trace, version
# CURL_OPENSSL_4" (libcurl 7.81 from the tarball), and samtools would load
# libhts 1.13 instead of the host's.
_mel_check_exec() {
  local bin="$1" rc=0
  [[ -f "$bin" ]] || { _mel_die "missing ${bin}"; return 1; }
  [[ -r "$bin" ]] || { _mel_die "cannot read ${bin}"; return 1; }
  [[ -x "$bin" ]] || { _mel_die "${bin} is not executable"; return 1; }
  env -u LD_LIBRARY_PATH -u LD_PRELOAD "$bin" --help >/dev/null 2>&1 </dev/null || rc=$?
  if [[ "$rc" -eq 126 || "$rc" -eq 127 ]]; then
    _mel_die "${bin} does not run on its own (exit ${rc}); its bundled libraries must resolve through RUNPATH \$ORIGIN/../lib (LD_LIBRARY_PATH is never set for it)"
    return 1
  fi
  return 0
}

# Print LD_LIBRARY_PATH ($2, default the current value) without DIR ($1).
_mel_ld_path_without() {
  local dir="$1" path="${2-${LD_LIBRARY_PATH:-}}" out="" e
  local -a parts=()
  IFS=: read -r -a parts <<<"$path"
  for e in "${parts[@]}"; do
    [[ -n "$e" && "$e" != "$dir" ]] || continue
    out+="${out:+:}$e"
  done
  printf '%s' "$out"
}

_mel_stage_bin_path() {
  local stage="$1"
  if [[ -f "$stage/bin/goliath-extractor" ]]; then
    printf '%s\n' "$stage/bin/goliath-extractor"
  elif [[ -f "$stage/bin/MethylExtractor" ]]; then
    printf '%s\n' "$stage/bin/MethylExtractor"
  fi
}

_mel_export_extractor_env() {
  local home="$1" bin="$2"
  cat >"$home/env.sh" <<ENV
# Source this file: source "$home/env.sh"
export GOLIATH_EXTRACTOR_HOME="$home"
export GOLIATH_EXTRACTOR_BIN="$bin"
export METHYL_EXTRACTOR_HOME="$home"
export METHYL_EXTRACTOR_BIN="$bin"
export HDF5_PLUGIN_PATH="$home/lib/hdf5_zstd_plugin"
# No LD_LIBRARY_PATH: the binary finds its bundled libs through RUNPATH.
export PATH="$home/bin:\$PATH"
ENV
  export GOLIATH_EXTRACTOR_HOME="$home"
  export GOLIATH_EXTRACTOR_BIN="$bin"
  export METHYL_EXTRACTOR_HOME="$home"
  export METHYL_EXTRACTOR_BIN="$bin"
  export HDF5_PLUGIN_PATH="$home/lib/hdf5_zstd_plugin"
  case ":${LD_LIBRARY_PATH:-}:" in
    *":$home/lib:"*)
      LD_LIBRARY_PATH="$(_mel_ld_path_without "$home/lib")"
      if [[ -n "$LD_LIBRARY_PATH" ]]; then export LD_LIBRARY_PATH; else unset LD_LIBRARY_PATH; fi
      ;;
  esac
}

# install_methyl_extractor_local TARBALL ARCH [EXPECTED_SHA256]
# Prints nothing on stdout; sets GOLIATH_EXTRACTOR_* / METHYL_EXTRACTOR_* /
# HDF5_PLUGIN_PATH in the caller (exported). Idempotent when sha256 matches.
install_methyl_extractor_local() {
  local tarball="$1" arch="$2" expected="${3:-}"
  local home bin stage_bin actual stamp stage
  [[ -f "$tarball" ]] || { _mel_die "GoliathExtractor tarball not found: ${tarball}"; return 1; }
  home="$(methyl_extractor_local_home "$arch")" || return 1
  bin="$(_mel_bin_in_home "$home" || true)"
  [[ -n "$bin" ]] || bin="$home/bin/goliath-extractor"
  stamp="$home/.tarball.sha256"
  actual="$(sha256sum "$tarball" | awk '{print $1}')" || { _mel_die "cannot read ${tarball}"; return 1; }
  if [[ -n "$expected" && "$actual" != "$expected" ]]; then
    _mel_die "checksum mismatch for ${tarball} (expected ${expected}, got ${actual})"
    return 1
  fi
  if [[ -f "$stamp" && "$(cat "$stamp" 2>/dev/null)" == "$actual" ]] \
    && bin="$(_mel_bin_in_home "$home" || true)" \
    && [[ -n "$bin" ]] && _mel_check_exec "$bin" 2>/dev/null; then
    _mel_info "GoliathExtractor already installed at ${home} (sha256 ${actual:0:12})" >&2
  else
    _mel_info "Extracting GoliathExtractor to local disk: ${home}" >&2
    mkdir -p "$(dirname "$home")" || { _mel_die "cannot create $(dirname "$home")"; return 1; }
    stage="$(mktemp -d "${home}.new.XXXXXX")" || return 1
    if ! tar -xzf "$tarball" -C "$stage"; then
      rm -rf "$stage"
      _mel_die "could not extract ${tarball} (corrupt or truncated archive?)"
      return 1
    fi
    stage_bin="$(_mel_stage_bin_path "$stage" || true)"
    if [[ -z "$stage_bin" ]]; then
      rm -rf "$stage"
      _mel_die "${tarball} has no bin/goliath-extractor or bin/MethylExtractor"
      return 1
    fi
    chmod 0755 "$stage" "$stage_bin"
    if ! _mel_check_exec "$stage_bin"; then
      rm -rf "$stage"
      return 1
    fi
    printf '%s\n' "$actual" >"$stage/.tarball.sha256"
    rm -rf "$home"
    mv "$stage" "$home"
    bin="$(_mel_bin_in_home "$home" || true)"
    _mel_check_exec "$bin" || return 1
  fi
  _mel_export_extractor_env "$home" "$bin"
  _mel_info "GoliathExtractor: ${bin}" >&2
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
    base="$(basename "$_tarball")"
    if [[ "$base" =~ goliath-extractor-linux-(aarch64|amd64) ]]; then
      _arch="${BASH_REMATCH[1]}"
    elif [[ "$base" =~ methyl-extractor-linux-(aarch64|amd64) ]]; then
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
    printf '%s\n' "$GOLIATH_EXTRACTOR_HOME"
  fi
fi
