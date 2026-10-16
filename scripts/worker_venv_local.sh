#!/bin/bash
# Install the worker Python venv from a release onto pod-local disk.
#
# Shared volumes (a Runpod network volume at /work, some NFS mounts) ignore
# exec bits, reject chmod/utime, and files written onto them can be listed but
# not always read back. A venv is full of executables (bin/python, entry
# points), so it can never live there. Release wheels and the lock stay on the
# shared volume; every machine copies them to local disk, verifies them, and
# builds its own venv.
#
# Location (first match wins):
#   GOLIATH_WORKER_VENV                  explicit venv dir
#   $GOLIATH_LOCAL_ROOT/venv-<arch>      (default /opt/goliath)
# A location under GOLIATH_WORK_MOUNT (default /work), or one whose parent
# cannot be created or written, falls back to /tmp/goliath/venv-<arch>.
#
# Install is offline first (release wheels/ only). If that fails, a warning
# is logged and the install is retried with a package index allowed
# (GOLIATH_PIP_INDEX_URL, default https://pypi.org/simple), still pinned by
# the lock and hash-checked when the lock carries hashes. Set
# GOLIATH_WORKER_VENV_ALLOW_INDEX=0 to forbid the online retry.
#
# Idempotent: <venv>/.goliath-venv-stamp records the release version, arch
# and requirements lock sha256. A matching stamp and a working interpreter
# skip the install.
#
# Source this file for worker_venv_local_dir / install_worker_venv_local, or run:
#   worker_venv_local.sh --release DIR [--arch KEY] [--recreate] [--print-dir]

_wvl_info() { echo "[INFO] $*" >&2; }
_wvl_warn() { echo "[WARN] $*" >&2; }
_wvl_die() { echo "[ERROR] $*" >&2; return 1; }

_WVL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# True when $1 is the work mount or below it.
_wvl_on_work() {
  local path="$1" work="${GOLIATH_WORK_MOUNT:-/work}"
  work="${work%/}"
  [[ -n "$work" ]] || return 1
  case "$path" in
    "$work"|"$work"/*) return 0 ;;
  esac
  return 1
}

# Print the pod-local venv directory for ARCH.
worker_venv_local_dir() {
  local arch="$1" dir parent
  if [[ -n "${GOLIATH_WORKER_VENV:-}" ]]; then
    dir="${GOLIATH_WORKER_VENV%/}"
  else
    dir="${GOLIATH_LOCAL_ROOT:-/opt/goliath}"
    dir="${dir%/}/venv-${arch}"
  fi
  parent="$(dirname "$dir")"
  if _wvl_on_work "$dir"; then
    _wvl_warn "${dir} is on the shared work mount; using /tmp instead"
    dir="/tmp/goliath/venv-${arch}"
  elif ! mkdir -p "$parent" 2>/dev/null || [[ ! -w "$parent" ]]; then
    if [[ ! -w "$dir" ]]; then
      _wvl_warn "cannot write ${parent}; using /tmp instead"
      dir="/tmp/goliath/venv-${arch}"
    fi
  fi
  printf '%s\n' "$dir"
}

_wvl_sha() { sha256sum "$1" 2>/dev/null | awk '{print $1}'; }

# Copy SRC to DEST and confirm size and sha256 match. Shared volumes can list
# a file that does not read back, so retry a few times before failing.
_wvl_copy_verified() {
  local src="$1" dest="$2" attempt src_sha dst_sha src_size dst_size
  for attempt in 1 2 3; do
    rm -f "$dest"
    if cp -f "$src" "$dest" 2>/dev/null; then
      src_size="$(stat -c %s "$src" 2>/dev/null || echo x)"
      dst_size="$(stat -c %s "$dest" 2>/dev/null || echo y)"
      src_sha="$(_wvl_sha "$src")"
      dst_sha="$(_wvl_sha "$dest")"
      if [[ "$src_size" == "$dst_size" && -n "$src_sha" && "$src_sha" == "$dst_sha" ]]; then
        return 0
      fi
    fi
    _wvl_warn "copy of ${src} did not verify (attempt ${attempt}); retrying"
    sleep "${GOLIATH_COPY_RETRY_SLEEP:-2}"
  done
  rm -f "$dest"
  _wvl_die "cannot read ${src} back from the shared volume"
}

_wvl_release_version() {
  local release="$1" ver=""
  if [[ -f "$release/manifest.json" ]]; then
    ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version",""))' "$release/manifest.json" 2>/dev/null || true)"
  fi
  [[ -n "$ver" ]] || ver="$(basename "$(readlink -f "$release" 2>/dev/null || printf '%s' "$release")")"
  printf '%s\n' "$ver"
}

# Print the lock file of RELEASE, or nothing.
worker_venv_release_lock() {
  local release="$1"
  if [[ -f "$release/requirements-worker.lock" ]]; then
    printf '%s\n' "$release/requirements-worker.lock"
  elif [[ -f "$release/requirements-worker.txt" ]]; then
    printf '%s\n' "$release/requirements-worker.txt"
  fi
}

_wvl_venv_works() {
  local venv="$1"
  [[ -x "$venv/bin/python" ]] || return 1
  "$venv/bin/python" -c 'import sys' >/dev/null 2>&1
}

# install_worker_venv_local RELEASE_DIR ARCH [--recreate]
# Sets and exports GOLIATH_WORKER_VENV to the ready venv.
install_worker_venv_local() {
  local release="$1" arch="$2" recreate="${3:-}"
  local venv lock lock_sha ver stamp expected stage f name installer
  [[ -d "$release" ]] || { _wvl_die "release directory not found: ${release}"; return 1; }
  lock="$(worker_venv_release_lock "$release")"
  [[ -n "$lock" ]] || { _wvl_die "missing ${release}/requirements-worker.lock"; return 1; }
  venv="$(worker_venv_local_dir "$arch")"
  ver="$(_wvl_release_version "$release")"
  mkdir -p "$(dirname "$venv")" || { _wvl_die "cannot create $(dirname "$venv")"; return 1; }
  stage="$(dirname "$venv")/.venv-stage-${arch}.$$"
  rm -rf "$stage"
  mkdir -p "$stage/wheels" || { _wvl_die "cannot create ${stage}"; return 1; }
  # Read the lock once, from a verified local copy.
  if ! _wvl_copy_verified "$lock" "$stage/$(basename "$lock")"; then
    rm -rf "$stage"
    return 1
  fi
  lock_sha="$(_wvl_sha "$stage/$(basename "$lock")")"
  stamp="$venv/.goliath-venv-stamp"
  expected="$(printf 'version=%s\narch=%s\nlock_sha256=%s\n' "$ver" "$arch" "$lock_sha")"
  if [[ "$recreate" != "--recreate" && -f "$stamp" && "$(cat "$stamp" 2>/dev/null)" == "$expected" ]] \
    && _wvl_venv_works "$venv"; then
    rm -rf "$stage"
    _wvl_info "worker venv ${venv} already matches release ${ver}"
    GOLIATH_WORKER_VENV="$venv"
    export GOLIATH_WORKER_VENV
    return 0
  fi
  if [[ -d "$release/wheels" ]]; then
    for f in "$release"/wheels/*; do
      [[ -f "$f" ]] || continue
      name="$(basename "$f")"
      if ! _wvl_copy_verified "$f" "$stage/wheels/$name"; then
        rm -rf "$stage"
        return 1
      fi
    done
  fi
  installer="$_WVL_SCRIPT_DIR/install_release.sh"
  [[ -f "$installer" ]] || installer="$release/runtime-bundle/scripts/install_release.sh"
  [[ -f "$installer" ]] || { rm -rf "$stage"; _wvl_die "install_release.sh not found"; return 1; }
  if [[ -z "${PYTHON_BIN:-}" ]] && ! command -v python3.12 >/dev/null 2>&1; then
    _wvl_warn "python3.12 not found; building the venv with python3"
    local PYTHON_BIN=python3
    export PYTHON_BIN
  fi
  _wvl_info "installing worker venv ${venv} from release ${ver} (verified local copy of wheels + lock)"
  if ! GOLIATH_WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}" \
    bash "$installer" --release-dir "$stage" --venv "$venv" --recreate-venv >&2; then
    local index="${GOLIATH_PIP_INDEX_URL:-https://pypi.org/simple}"
    if [[ "${GOLIATH_WORKER_VENV_ALLOW_INDEX:-1}" != "1" ]]; then
      rm -rf "$stage"
      _wvl_die "offline worker venv install into ${venv} failed (release wheels/ incomplete?) and GOLIATH_WORKER_VENV_ALLOW_INDEX=0"
      return 1
    fi
    _wvl_warn "offline install from release wheels/ failed; retrying with package index ${index} (lock pins still enforced)"
    if ! GOLIATH_WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}" \
      bash "$installer" --release-dir "$stage" --venv "$venv" --recreate-venv --index-url "$index" >&2; then
      rm -rf "$stage"
      _wvl_die "worker venv install into ${venv} failed (offline and via ${index})"
      return 1
    fi
  fi
  rm -rf "$stage"
  _wvl_venv_works "$venv" || { _wvl_die "${venv}/bin/python does not run"; return 1; }
  printf '%s\n' "$expected" >"$stamp"
  GOLIATH_WORKER_VENV="$venv"
  export GOLIATH_WORKER_VENV
  _wvl_info "worker venv ready: ${venv}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  _rel="" _arch="" _recreate="" _print=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --release) _rel="${2:-}"; shift 2 ;;
      --arch) _arch="${2:-}"; shift 2 ;;
      --recreate) _recreate="--recreate"; shift ;;
      --print-dir) _print=1; shift ;;
      -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done
  if [[ -z "$_arch" ]]; then
    # shellcheck source=detect_platform.sh
    source "$_WVL_SCRIPT_DIR/detect_platform.sh"
    _arch="$(platform_arch_key "$(detect_uname_arch)")"
  fi
  if [[ "$_print" -eq 1 && -z "$_rel" ]]; then
    worker_venv_local_dir "$_arch"
    exit 0
  fi
  [[ -n "$_rel" ]] || { echo "--release is required" >&2; exit 1; }
  install_worker_venv_local "$_rel" "$_arch" "$_recreate"
  [[ "$_print" -eq 1 ]] && printf '%s\n' "$GOLIATH_WORKER_VENV"
  exit 0
fi
