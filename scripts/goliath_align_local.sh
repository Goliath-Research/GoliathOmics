#!/usr/bin/env bash
# Install GoliathAlign host runtime from a release tarball onto local disk.
# Shared /work cannot exec native binaries reliably; same pattern as MethylExtractor.
#
# Source this file, or: goliath_align_local.sh --tarball PATH [--arch amd64] [--sha256 HEX]

_gal_info() { echo "[INFO] $*"; }
_gal_die() { echo "[ERROR] $*" >&2; return 1; }

_gal_on_work() {
  local path="$1" work="${GOLIATH_WORK_MOUNT:-/work}"
  work="${work%/}"
  case "$path" in "$work"|"$work"/*) return 0 ;; esac
  return 1
}

goliath_align_local_home() {
  local arch="${1:-amd64}" home parent
  if [[ -n "${GOLIATH_ALIGN_HOME:-}" ]]; then
    home="${GOLIATH_ALIGN_HOME%/}"
  else
    home="${GOLIATH_LOCAL_ROOT:-/opt/goliath}"
    home="${home%/}/goliath-align-${arch}"
  fi
  parent="$(dirname "$home")"
  if _gal_on_work "$home"; then
    echo "[WARN] ${home} is on the shared work mount; using /tmp instead" >&2
    home="/tmp/goliath/goliath-align-${arch}"
  elif ! mkdir -p "$parent" 2>/dev/null || [[ ! -w "$parent" ]]; then
    echo "[WARN] cannot write ${parent}; using /tmp instead" >&2
    home="/tmp/goliath/goliath-align-${arch}"
  fi
  printf '%s\n' "$home"
}

install_goliath_align_local() {
  local tarball="$1" arch="${2:-amd64}" want_sha="${3:-}" home stamp got
  [[ -f "$tarball" ]] || { _gal_die "missing tarball $tarball"; return 1; }
  home="$(goliath_align_local_home "$arch")"
  stamp="$home/.tarball.sha256"
  if [[ -n "$want_sha" && -f "$stamp" && -x "$home/bin/methylGrapher" ]]; then
    got="$(tr -d '[:space:]' <"$stamp")"
    if [[ "$got" == "$want_sha" ]]; then
      _gal_info "GoliathAlign host runtime unchanged at $home"
      export GOLIATH_ALIGN_HOME="$home"
      export METHYL_METHYLGRAPHER_BIN="$home/bin/methylGrapher"
      export PATH="$home/bin:${PATH}"
      return 0
    fi
  fi
  _gal_info "installing GoliathAlign host runtime → $home"
  rm -rf "$home"
  mkdir -p "$home"
  tar -C "$home" --strip-components=1 -xzf "$tarball"
  [[ -x "$home/bin/methylGrapher" ]] || { _gal_die "no bin/methylGrapher after extract"; return 1; }
  if [[ -n "$want_sha" ]]; then
    printf '%s\n' "$want_sha" >"$stamp"
  else
    sha256sum "$tarball" | awk '{print $1}' >"$stamp"
  fi
  # Smoke: help must not be 126/127
  local rc=0
  "$home/bin/methylGrapher" help >/dev/null 2>&1 || rc=$?
  if [[ "$rc" -eq 126 || "$rc" -eq 127 ]]; then
    _gal_die "methylGrapher does not execute from $home (rc=$rc)"
    return 1
  fi
  export GOLIATH_ALIGN_HOME="$home"
  export METHYL_METHYLGRAPHER_BIN="$home/bin/methylGrapher"
  export METHYL_METHYLGRAPHER_MODE="${METHYL_METHYLGRAPHER_MODE:-host}"
  export PATH="$home/bin:${PATH}"
  _gal_info "GoliathAlign on local disk: $METHYL_METHYLGRAPHER_BIN"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  TB="" ARCH=amd64 SHA=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tarball) TB="${2:-}"; shift 2 ;;
      --arch) ARCH="${2:-}"; shift 2 ;;
      --sha256) SHA="${2:-}"; shift 2 ;;
      --print-home) goliath_align_local_home "${2:-amd64}"; exit 0 ;;
      *) echo "unknown: $1" >&2; exit 1 ;;
    esac
  done
  install_goliath_align_local "$TB" "$ARCH" "$SHA"
fi
