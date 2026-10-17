#!/usr/bin/env bash
# Provider-neutral shared-worker start. start_shared_worker
#
# Every cluster mounts shared read/write storage at /work (override with
# GOLIATH_WORK_MOUNT). New data uses B2_* (S3 protocol). Long-term objects
# use LONGTERM_* (S3 protocol, myQNAPcloud on the current cluster). Those
# names are the inputs. This script does not know which cloud provided the
# machine. A provider adapter (deploy/runpod/) may set GOLIATH_NODE_ID and
# GOLIATH_START_KEEPALIVE before exec.
#
# Required: WORKER_API_BASE (the gateway URL). Secrets stay on local disk
# at mode 0600. Files on /work are created world-readable (umask 0022,
# directories 0755, files 0644). When GOLIATH_SHARED_GROUP names a group
# that exists here, directories are 2775, files are 0664, and umask is 0002.
# chmod and chgrp are attempted. If the filesystem rejects them, this script
# warns and continues. A local secret that cannot be mode 0600 is fatal.
#
# GOLIATH_NODE_ID names this machine's directory under /work/goliath/nodes.
# When it is unset, the hostname is used, then /etc/machine-id.
# GOLIATH_START_KEEPALIVE, when set to an executable, is exec'd after the
# worker is up and also after a fatal error. When it is unset, success exits
# 0 (systemd or the supervisor already holds the worker) and failure exits 1.
# GOLIATH_START_NO_EXEC=1 makes failures exit 1 instead of running a keepalive.
#
# Secrets are read only from the environment. This script never prints them.
# Do not run it under set -x.

set +x
set -euo pipefail
set -E

START_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${GOLIATH_ROOT:-/work/goliath}"
WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}"
ARCH="${GOLIATH_WORKER_ARCH:-amd64}"
LOG_DIR="${GOLIATH_LOG_DIR:-}"
PRIVATE_DIR=""
PROMOTE_LOCK=""
CHMOD_WORK_QUIET=0
CHMOD_WORK_FAILED=0
CHMOD_WORK_WARNED=0
# Runtime logs (methyl-worker.log, supervisor pid) stay on local disk, never
# on the shared work mount: /work carries input/output data only. The log is
# rotated by size: GOLIATH_LOG_MAX_BYTES per file, GOLIATH_LOG_KEEP old files.
GOLIATH_LOG_MAX_BYTES="${GOLIATH_LOG_MAX_BYTES:-52428800}"
GOLIATH_LOG_KEEP="${GOLIATH_LOG_KEEP:-3}"
export METHYL_WORKER_TOKEN_FILE="${METHYL_WORKER_TOKEN_FILE:-/etc/methyl/worker-token}"
export GOLIATH_ENV="${GOLIATH_ENV:-goliath-test}"

GPU_DESC=""
HOST_SHORT=""

log() { printf '[start-shared-worker] %s\n' "$*"; }

exec_keepalive() {
  local keepalive="${GOLIATH_START_KEEPALIVE:-}"
  [[ -n "$keepalive" ]] || return 1
  if [[ -x "$keepalive" ]]; then
    log "exec ${keepalive}"
    if [[ "$#" -gt 0 ]]; then
      exec "$keepalive" "$@"
    fi
    exec "$keepalive"
  fi
  # The shared volume drops exec bits; run a keepalive script stored there
  # with bash instead of exec'ing it.
  if [[ -f "$keepalive" && "$keepalive" == *.sh ]]; then
    log "exec bash ${keepalive}"
    exec bash "$keepalive" "$@"
  fi
  log "warning: keepalive ${keepalive} is not executable"
  return 1
}

die() {
  release_promote_lock
  trap - ERR
  printf '[start-shared-worker] ERROR: %s\n' "$*" >&2
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    exit 1
  fi
  if [[ -n "${GOLIATH_START_KEEPALIVE:-}" ]]; then
    printf '[start-shared-worker] keeping the process alive\n' >&2
    exec_keepalive || true
  fi
  exit 1
}

on_err() {
  local rc=$?
  release_promote_lock
  trap - ERR
  printf '[start-shared-worker] ERROR: command failed at line %s (exit %s)\n' "$1" "$rc" >&2
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    exit 1
  fi
  if [[ -n "${GOLIATH_START_KEEPALIVE:-}" ]]; then
    printf '[start-shared-worker] keeping the process alive\n' >&2
    exec_keepalive || true
  fi
  exit 1
}
trap 'on_err "${BASH_LINENO[0]}"' ERR

wait_for_work() {
  local i
  for ((i = 1; i <= 90; i++)); do
    if [[ -d "$WORK_MOUNT" && -w "$WORK_MOUNT" ]]; then
      return 0
    fi
    sleep 2
  done
  die "persistent volume ${WORK_MOUNT} is not mounted and writable"
}

node_id() {
  local id="${GOLIATH_NODE_ID:-}"
  if [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    printf '%s' "$id"
    return 0
  fi
  local host mid
  host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
  if [[ "$host" =~ ^[A-Za-z0-9._-]+$ ]]; then
    printf '%s' "$host"
    return 0
  fi
  if [[ -r /etc/machine-id ]]; then
    mid="$(tr -cd 'A-Za-z0-9._-' </etc/machine-id | head -c 32)"
    if [[ -n "$mid" ]]; then
      printf '%s' "$mid"
      return 0
    fi
  fi
  printf '%s' "unknown-node"
}

node_root() {
  printf '%s/nodes/%s' "$ROOT" "$(node_id)"
}

# A named group that exists on this machine. Absent group: world-readable
# modes. Present group: setgid directories and group-writable files.
shared_group() {
  local group="${GOLIATH_SHARED_GROUP:-}"
  [[ -n "$group" ]] || return 1
  getent group "$group" >/dev/null 2>&1
}

apply_work_umask() {
  if shared_group; then
    umask 002
  else
    umask 022
  fi
}

work_dir_mode() {
  if shared_group; then
    printf '2775'
  else
    printf '0755'
  fi
}

work_file_mode() {
  if shared_group; then
    printf '0664'
  else
    printf '0644'
  fi
}

path_on_work() {
  local path="$1"
  local mount="${WORK_MOUNT%/}"
  [[ "$path" == "$mount" || "$path" == "$mount"/* ]]
}

chmod_work() {
  local mode="$1"
  shift
  local path group="${GOLIATH_SHARED_GROUP:-}"
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    if ! chmod "$mode" "$path" 2>/dev/null; then
      if [[ "$CHMOD_WORK_QUIET" == "1" || "$CHMOD_WORK_WARNED" == "1" ]]; then
        CHMOD_WORK_FAILED=$((CHMOD_WORK_FAILED + 1))
      else
        # Expected on shared global volumes (fixed owner/mode). One line per
        # boot; later rejections are only counted.
        CHMOD_WORK_WARNED=1
        log "note: chmod ${mode} ${path} was not applied (expected on a shared volume that fixes modes); continuing, further chmod/chgrp notices suppressed"
      fi
    fi
    if shared_group; then
      if ! chgrp "$group" "$path" 2>/dev/null; then
        if [[ "$CHMOD_WORK_QUIET" == "1" || "$CHMOD_WORK_WARNED" == "1" ]]; then
          CHMOD_WORK_FAILED=$((CHMOD_WORK_FAILED + 1))
        else
          CHMOD_WORK_WARNED=1
          log "note: chgrp ${group} ${path} was not applied (expected on a shared volume); continuing, further chmod/chgrp notices suppressed"
        fi
      fi
    fi
  done
}

private_dir() {
  if [[ -n "$PRIVATE_DIR" && -d "$PRIVATE_DIR" ]]; then
    printf '%s' "$PRIVATE_DIR"
    return 0
  fi
  local base="${GOLIATH_PRIVATE_DIR:-}"
  if [[ -z "$base" ]]; then
    if [[ -d /run && -w /run ]]; then
      base="/run/goliath"
    else
      base="/tmp/goliath-private"
    fi
  fi
  if path_on_work "$base"; then
    die "private dir ${base} must not be on ${WORK_MOUNT}"
  fi
  local old
  old="$(umask)"
  umask 077
  mkdir -p "$base"
  umask "$old"
  if ! chmod 700 "$base"; then
    die "cannot protect ${base}"
  fi
  PRIVATE_DIR="$base"
  printf '%s' "$PRIVATE_DIR"
}

release_promote_lock() {
  if [[ -n "$PROMOTE_LOCK" ]]; then
    rm -rf "$PROMOTE_LOCK"
    PROMOTE_LOCK=""
  fi
}

# mkdir is the only create that is atomic on a typical filesystem. Some shared
# volumes (a Runpod global volume, some NFS exports) do not support file
# locking, so a crashed holder is stolen after 30 minutes.
acquire_promote_lock() {
  local lock="$ROOT/locks/release-promote"
  local i now mtime
  mkdir -p "$ROOT/locks"
  for ((i = 1; i <= 60; i++)); do
    if mkdir "$lock" 2>/dev/null; then
      printf '%s\n' "$(node_id)" >"$lock/owner"
      PROMOTE_LOCK="$lock"
      log "acquired release promote lock ${lock}"
      return 0
    fi
    if [[ -f "$lock/owner" ]]; then
      now="$(date +%s)"
      mtime="$(stat -c %Y "$lock/owner" 2>/dev/null || echo 0)"
      if [[ "$((now - mtime))" -gt 1800 ]]; then
        log "warning: removing stale promote lock ${lock}"
        rm -rf "$lock"
        continue
      fi
    fi
    sleep 2
  done
  die "timed out waiting for ${lock}"
}

# Print the release directory ROOT/current points at, or nothing (exit 1)
# when it is missing, empty or invalid. Shared /work volumes cannot store
# symlinks (ln -s left a 0-byte regular file), so current is a pointer file
# holding the release path; a symlink or a real directory (older layouts) is
# still accepted.
goliath_current_dir() {
  local root="${1%/}" cur target=""
  cur="$root/current"
  if [[ -d "$cur" ]]; then
    target="$(cd "$cur" 2>/dev/null && pwd -P)" || return 1
  elif [[ -f "$cur" && -s "$cur" ]]; then
    target="$(head -n 1 "$cur" 2>/dev/null | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$target" || "$target" == /* ]] || target="$root/$target"
  fi
  [[ -n "$target" && -d "$target" ]] || return 1
  printf '%s\n' "$target"
}

# Release dir behind $ROOT/current, or the literal path when unresolved.
cur_dir() {
  goliath_current_dir "$ROOT" 2>/dev/null || printf '%s\n' "$ROOT/current"
}

# Version from a manifest.json, or nothing.
manifest_version() {
  [[ -f "$1" ]] || return 0
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version") or "")' "$1" 2>/dev/null || true
}

# Release version the bootstrap resolved as newest (GOLIATH_RELEASE_TAG, else
# $ROOT/bin/.release written by goliath-worker-bootstrap.sh), without the
# goliathomics-/v prefix. Empty when unknown.
wanted_release_version() {
  local tag="${GOLIATH_RELEASE_TAG:-}"
  if [[ -z "$tag" && -f "$ROOT/bin/.release" ]]; then
    tag="$(head -n 1 "$ROOT/bin/.release" 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  tag="${tag#goliathomics-}"
  tag="${tag#v}"
  [[ "$tag" =~ ^[A-Za-z0-9._+-]+$ ]] && printf '%s\n' "$tag"
  return 0
}

# True when version $1 is older than $2 (numeric dotted compare).
version_older() {
  python3 - "$1" "$2" <<'PY'
import re, sys
def key(v):
    return [int(x) if x.isdigit() else x for x in re.split(r"[.+-]", v)]
try:
    sys.exit(0 if key(sys.argv[1]) < key(sys.argv[2]) else 1)
except TypeError:
    sys.exit(1)
PY
}

resolve_tool() {
  local override="$1"
  local name="$2"
  local candidate
  if [[ -n "$override" ]]; then
    [[ -f "$override" ]] || die "missing ${name} at ${override}"
    printf '%s' "$override"
    return 0
  fi
  for candidate in \
    "$(cur_dir)/runtime-bundle/scripts/$name" \
    "$(cur_dir)/scripts/$name"
  do
    if [[ -f "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

curl_download() {
  local url="$1"
  local dest="$2"
  local token="$3"
  local accept="${4:-}"
  local header="" old_umask rc=0
  local -a args=(-fsSL --retry 3 --retry-delay 2)
  if [[ -n "$token" ]]; then
    header="$(private_dir)/github.headers"
    old_umask="$(umask)"
    umask 077
    printf 'Authorization: Bearer %s\n' "$token" >"$header"
    umask "$old_umask"
    if ! chmod 600 "$header"; then
      die "cannot protect the GitHub auth header"
    fi
    args+=(-H "@${header}")
  fi
  if [[ -n "$accept" ]]; then
    args+=(-H "Accept: ${accept}")
  fi
  args+=(-o "$dest" "$url")
  curl "${args[@]}" || rc=$?
  if [[ -n "$header" ]]; then
    rm -f "$header"
  fi
  [[ "$rc" -eq 0 ]] || die "release download failed"
}

download_latest_release() {
  local repo="$1"
  local token="$2"
  local dest="$3"
  local api meta asset_spec asset_id asset_name asset_url
  if [[ -n "${GOLIATH_RELEASE_TAG:-}" ]]; then
    api="https://api.github.com/repos/${repo}/releases/tags/${GOLIATH_RELEASE_TAG}"
  else
    api="https://api.github.com/repos/${repo}/releases/latest"
  fi
  meta="$(node_root)/tmp/release.json"
  mkdir -p "$(node_root)/tmp"
  curl_download "$api" "$meta" "$token" "application/vnd.github+json"
  asset_spec="$(python3 -c 'import json,sys
rel=json.load(open(sys.argv[1], encoding="utf-8"))
for asset in rel.get("assets") or []:
    name=asset.get("name") or ""
    if name.startswith("goliathomics-release-") and name.endswith(".tar.gz"):
        print(asset["id"])
        print(name)
        break
' "$meta")"
  rm -f "$meta"
  [[ -n "$asset_spec" ]] || die "GitHub release has no goliathomics-release-*.tar.gz asset"
  asset_id="${asset_spec%%$'\n'*}"
  asset_name="${asset_spec#*$'\n'}"
  [[ "$asset_id" =~ ^[0-9]+$ ]] || die "release asset id was not numeric"
  log "downloading ${asset_name}"
  asset_url="https://api.github.com/repos/${repo}/releases/assets/${asset_id}"
  curl_download "$asset_url" "$dest" "$token" "application/octet-stream"
}

# Apply the intended /work modes to an unpacked release tree with chmod_work.
# Directories get work_dir_mode, files get work_file_mode, and files that were
# executable in the archive keep an execute bit (0755, or 0775 with a shared
# group). Volumes that reject chmod/chgrp (a Runpod global network volume,
# root-squashed NFS) produce one summary warning instead of one per file.
apply_release_modes() {
  local tree="$1"
  local dir_mode file_mode exec_mode path
  dir_mode="$(work_dir_mode)"
  file_mode="$(work_file_mode)"
  if shared_group; then
    exec_mode="0775"
  else
    exec_mode="0755"
  fi
  CHMOD_WORK_QUIET=1
  CHMOD_WORK_FAILED=0
  while IFS= read -r -d '' path; do
    chmod_work "$dir_mode" "$path"
  done < <(find "$tree" -type d -print0)
  while IFS= read -r -d '' path; do
    if [[ -x "$path" ]]; then
      chmod_work "$exec_mode" "$path"
    else
      chmod_work "$file_mode" "$path"
    fi
  done < <(find "$tree" -type f -print0)
  CHMOD_WORK_QUIET=0
  if [[ "$CHMOD_WORK_FAILED" -gt 0 ]]; then
    log "warning: ${CHMOD_WORK_FAILED} chmod/chgrp operations under ${tree} were not applied (filesystem rejects them); continuing"
  fi
  CHMOD_WORK_FAILED=0
}

# Unpack a release tarball without asking the destination filesystem to
# preserve owner, mode or mtime. Shared volumes such as a Runpod global
# network volume (nobody:nogroup, mode 777) reject chown/chmod/utime, and GNU
# tar turns each rejection into exit 2. We extract into a local staging
# directory first (never on /work), then copy it with plain cp -R (no -p/-a,
# so no owner or mtime is preserved). Real tar errors (corrupt or truncated
# archive) still fail.
extract_release() {
  local tarball="$1"
  local dest="$2"
  local stage_base="${GOLIATH_STAGE_DIR:-${TMPDIR:-/tmp}}"
  local stage
  if path_on_work "$stage_base"; then
    stage_base="/tmp"
  fi
  mkdir -p "$stage_base"
  stage="$(mktemp -d "${stage_base%/}/goliath-release.XXXXXX")"
  if ! tar --no-same-owner --no-same-permissions -m -xzf "$tarball" -C "$stage"; then
    rm -rf "$stage"
    die "could not extract release bundle ${tarball} (corrupt or truncated archive?)"
  fi
  if ! cp -R "$stage/." "$dest/"; then
    rm -rf "$stage"
    die "could not copy the release bundle into ${dest}"
  fi
  rm -rf "$stage"
  apply_release_modes "$dest"
}

# Use the release behind $ROOT/current unless it is missing, empty/invalid
# (a 0-byte file left by ln -s on a volume without symlinks), or older than
# the release the bootstrap resolved as newest; then download and promote.
current_is_usable() {
  local have want
  have="$(goliath_current_dir "$ROOT")" || return 1
  want="$(wanted_release_version)"
  [[ -z "$want" ]] && return 0
  ! version_older "$(manifest_version "$have/manifest.json")" "$want"
}

# Release directory this boot runs: set by ensure_current to the release it
# kept or just promoted. Later steps (maybe_reexec) use this explicit path,
# never a fresh read of $ROOT/current, which another pod may be flipping.
TARGET_RELEASE=""

ensure_current() {
  local have="" have_ver="" want
  want="$(wanted_release_version)"
  if have="$(goliath_current_dir "$ROOT")"; then
    have_ver="$(manifest_version "$have/manifest.json")"
  elif [[ -e "$ROOT/current" || -L "$ROOT/current" ]]; then
    log "warning: $ROOT/current is empty or does not name a release directory; treating it as missing"
  fi
  if current_is_usable; then
    log "using existing $ROOT/current -> ${have} (release ${have_ver:-unknown})"
    TARGET_RELEASE="$have"
    return 0
  fi
  [[ -n "$have" ]] && log "current release ${have_ver:-unknown} is older than ${want}; refreshing"
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  local url="${GOLIATH_RELEASE_URL:-}"
  local repo="${GOLIATH_GITHUB_REPO:-Goliath-Research/GoliathOmics}"
  if [[ -z "$url" && -z "$token" && ! ( -n "$want" && -f "$ROOT/releases/$want/manifest.json" ) ]]; then
    if [[ -n "$have" ]]; then
      log "warning: no GH_TOKEN/GITHUB_TOKEN or GOLIATH_RELEASE_URL to fetch ${want}; staying on ${have_ver}"
      TARGET_RELEASE="$have"
      return 0
    fi
    die "$ROOT/current is missing and neither GH_TOKEN/GITHUB_TOKEN nor GOLIATH_RELEASE_URL is set; cannot bootstrap a release bundle"
  fi
  command -v python3 >/dev/null 2>&1 || die "python3 is required to read the release manifest"
  command -v curl >/dev/null 2>&1 || die "curl is required to download the release bundle"
  # One pod publishes the shared release. Others wait, then use current.
  # The lock is advisory: this volume does not support file locking.
  acquire_promote_lock
  if current_is_usable; then
    TARGET_RELEASE="$(goliath_current_dir "$ROOT")"
    log "using existing $ROOT/current -> ${TARGET_RELEASE}"
    release_promote_lock
    return 0
  fi
  apply_work_umask
  mkdir -p "$ROOT/releases" "$(node_root)/tmp"
  local tarball partial dest ver promote
  tarball="$(node_root)/tmp/goliathomics-release.tar.gz"
  if [[ -n "$want" && -f "$ROOT/releases/$want/manifest.json" && -z "$url" ]]; then
    log "release ${want} is already on the volume; promoting it"
    : >"$tarball"
  elif [[ -n "$url" ]]; then
    log "bootstrapping release from GOLIATH_RELEASE_URL"
    curl_download "$url" "$tarball" "$token"
  else
    log "bootstrapping ${want:+goliathomics-${want} from }the GitHub releases of ${repo}"
    GOLIATH_RELEASE_TAG="${GOLIATH_RELEASE_TAG:-${want:+goliathomics-${want}}}" \
      download_latest_release "$repo" "$token" "$tarball"
  fi
  partial="$ROOT/releases/.partial-$(node_id)"
  rm -rf "$partial"
  if [[ -s "$tarball" ]]; then
    mkdir -p "$partial"
    extract_release "$tarball" "$partial"
  else
    # The release tree is already published; only its manifest is needed to
    # name it (the existing directory is reused below, nothing is copied).
    mkdir -p "$partial"
    cp "$ROOT/releases/$want/manifest.json" "$partial/"
  fi
  rm -f "$tarball"
  [[ -f "$partial/manifest.json" ]] || die "release bundle has no manifest.json"
  ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version",""))' "$partial/manifest.json")"
  [[ "$ver" =~ ^[A-Za-z0-9._+-]+$ ]] || die "release manifest version is missing or not a safe path component"
  dest="$ROOT/releases/$ver"
  if [[ ! -d "$dest" ]]; then
    # Same-filesystem rename. Not atomic on every shared volume (a Runpod
    # global volume is one that does not provide it). current.ready is the
    # commit point, written after promote returns.
    if ! mv "$partial" "$dest"; then
      if [[ ! -d "$dest" ]]; then
        die "could not publish ${dest}"
      fi
      log "release ${ver} appeared while publishing; using the existing tree"
      rm -rf "$partial"
    fi
  else
    log "release ${ver} is already on the volume"
    rm -rf "$partial"
  fi
  promote="$dest/runtime-bundle/scripts/promote_release.sh"
  [[ -f "$promote" ]] || die "release bundle has no runtime-bundle/scripts/promote_release.sh"
  # --skip-venv: the venv is pod-local and install_local_venv builds it on
  # every boot. Promotion writes nothing executable onto the volume.
  local -a promote_args=(--root "$ROOT" --release "$dest" --arch "$ARCH" --skip-docker-pull --skip-venv)
  if [[ -n "${WORKER_API_BASE:-}" ]]; then
    promote_args+=(--worker-api-base "$WORKER_API_BASE")
  fi
  log "promoting ${dest}"
  export GOLIATH_SHARED_WORK=1
  bash "$promote" "${promote_args[@]}"
  local dest_abs
  dest_abs="$(cd "$dest" && pwd -P)"
  if [[ "$(goliath_current_dir "$ROOT" || true)" != "$dest_abs" ]]; then
    # A release whose promote_release.sh predates the pointer file ran ln -s,
    # which leaves an empty regular file on volumes without symlinks.
    log "promote left $ROOT/current unresolved; writing the pointer file"
    [[ -d "$ROOT/current" && ! -L "$ROOT/current" ]] && die "$ROOT/current is a directory; move it aside"
    printf '%s\n' "$dest_abs" >"$ROOT/.current.$$"
    if ! mv -fT "$ROOT/.current.$$" "$ROOT/current"; then
      rm -f "$ROOT/.current.$$"
      printf '%s\n' "$dest_abs" >"$ROOT/current"
    fi
    [[ "$(goliath_current_dir "$ROOT" || true)" == "$dest_abs" ]] \
      || die "cannot point $ROOT/current at ${dest}"
  fi
  printf '%s\n' "$dest" >"$ROOT/.current.ready.$$"
  mv -f "$ROOT/.current.ready.$$" "$ROOT/current.ready"
  TARGET_RELEASE="$dest_abs"
  release_promote_lock
}

# MethylExtractor is native code and the shared volume cannot run binaries
# extracted onto it (exec bits are ignored, extracted files do not read back).
# Promotion happens once under the lock; this local install runs on every pod
# boot. The verified tarball stays in the release dir on the volume; the
# binary goes to METHYL_EXTRACTOR_HOME, default
# ${GOLIATH_LOCAL_ROOT:-/opt/goliath}/methyl-extractor-<arch> (never under
# the work mount). Unchanged tarballs are skipped.
install_local_extractor() {
  local manifest helper="" candidate tar_name sha release
  manifest="$(cur_dir)/manifest.json"
  if [[ ! -f "$manifest" ]]; then
    log "no ${manifest}; skipping local MethylExtractor install"
    return 0
  fi
  for candidate in "$START_SCRIPT_DIR/methyl_extractor_local.sh" \
    "$(cur_dir)/runtime-bundle/scripts/methyl_extractor_local.sh"; do
    if [[ -f "$candidate" ]]; then
      helper="$candidate"
      break
    fi
  done
  [[ -n "$helper" ]] || die "methyl_extractor_local.sh not found next to this script or in the release bundle"
  # shellcheck source=methyl_extractor_local.sh
  source "$helper"
  release="$(cur_dir)"
  tar_name="$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1], encoding="utf-8")); print(m.get("artifacts",{}).get(sys.argv[2],{}).get("methyl_extractor",""))' "$manifest" "$ARCH")"
  sha="$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1], encoding="utf-8")); print(m.get("artifacts",{}).get(sys.argv[2],{}).get("sha256",""))' "$manifest" "$ARCH")"
  if [[ -z "$tar_name" ]]; then
    log "release manifest has no MethylExtractor tarball for ${ARCH}; skipping local install"
    return 0
  fi
  install_methyl_extractor_local "$release/$tar_name" "$ARCH" "$sha" \
    || die "local MethylExtractor install from ${release}/${tar_name} failed"
  log "MethylExtractor on local disk: ${METHYL_EXTRACTOR_BIN}"
}

# The worker venv (bin/python, methyl-worker) cannot run from the shared
# volume either. Every pod boot builds it on local disk from the release's
# wheels/ and requirements-worker.lock, copied off the volume and verified
# first. Default ${GOLIATH_LOCAL_ROOT:-/opt/goliath}/venv-<arch>; a location
# on the work mount falls back to /tmp/goliath. A stamp with the release
# version and lock sha256 skips an unchanged install.
LOCAL_VENV_READY=0
install_local_venv() {
  local helper="" candidate release
  for candidate in "$START_SCRIPT_DIR/worker_venv_local.sh" \
    "$(cur_dir)/runtime-bundle/scripts/worker_venv_local.sh"; do
    if [[ -f "$candidate" ]]; then
      helper="$candidate"
      break
    fi
  done
  [[ -n "$helper" ]] || die "worker_venv_local.sh not found next to this script or in the release bundle"
  # shellcheck source=worker_venv_local.sh
  source "$helper"
  release="$(cur_dir)"
  if [[ -z "$(worker_venv_release_lock "$release")" ]]; then
    GOLIATH_WORKER_VENV="$(worker_venv_local_dir "$ARCH")"
    export GOLIATH_WORKER_VENV
    log "no requirements-worker.lock in ${release}; using existing venv ${GOLIATH_WORKER_VENV}"
    return 0
  fi
  install_worker_venv_local "$release" "$ARCH" \
    || die "local worker venv install from ${release} failed"
  LOCAL_VENV_READY=1
  log "worker venv on local disk: ${GOLIATH_WORKER_VENV}"
}

# Shared trees (samples, genomes, site, releases, image cache) stay on the
# volume. Per-node scratch and state are under nodes/<id>; logs are local. Docker overlay
# storage cannot live on this network filesystem.
# Install the external tools the pipeline needs (samtools, bedtools, setfacl,
# ...; scripts/system_tools.list) on this host's local disk before anything
# uses them. A tool that cannot be installed stops the start: a worker without
# it would silently drop pipeline steps.
ensure_system_deps() {
  local installer=""
  if [[ -n "${GOLIATH_SYSTEM_DEPS_SCRIPT:-}" ]]; then
    installer="$(resolve_tool "$GOLIATH_SYSTEM_DEPS_SCRIPT" install_system_deps.sh)"
  elif [[ -f "$START_SCRIPT_DIR/install_system_deps.sh" && -f "$START_SCRIPT_DIR/system_tools.list" ]]; then
    installer="$START_SCRIPT_DIR/install_system_deps.sh"
  else
    installer="$(resolve_tool "" install_system_deps.sh)" \
      || die "install_system_deps.sh not found next to this script or in the release bundle"
  fi
  local -a args=()
  # The release carries offline .deb bundles (system-deps/<os>-<ver>-<arch>/);
  # the installer copies them to local disk before installing.
  if [[ -d "$(cur_dir)/system-deps" ]]; then
    args+=(--bundle "$(cur_dir)/system-deps")
  fi
  log "checking worker system tools ($installer)"
  bash "$installer" "${args[@]}" || die "required system tools could not be installed; see the install-system-deps errors above"
}

ensure_work_layout() {
  local work_root init
  work_root="$(dirname "$ROOT")"
  init=""
  if init="$(resolve_tool "" init_work_layout.sh)"; then
    log "ensuring work layout under ${work_root}"
    bash "$init" --work "$work_root" || die "init_work_layout.sh failed"
  else
    log "init_work_layout.sh not in the release; creating samples, genomes, and goliath directories"
    mkdir -p \
      "$work_root/samples" \
      "$work_root/projects" \
      "$work_root/cache" \
      "$work_root/genomes" \
      "$work_root/site" \
      "$ROOT/bin" \
      "$ROOT/env" \
      "$ROOT/logs" \
      "$ROOT/tmp" \
      "$ROOT/releases"
    chmod_work "$(work_dir_mode)" \
      "$work_root/samples" "$work_root/projects" "$work_root/cache" \
      "$work_root/genomes" "$work_root/site" "$ROOT"
  fi
  apply_work_umask
  mkdir -p "$ROOT/images" "$ROOT/bin" "$(node_root)/tmp" "$(node_root)/scratch" "$(node_root)/status" "$(node_root)/env"
  chmod_work "$(work_dir_mode)" \
    "$ROOT/images" "$ROOT/bin" "$ROOT/locks" \
    "$(node_root)" "$(node_root)/tmp" \
    "$(node_root)/scratch" "$(node_root)/status" "$(node_root)/env"
  warn_docker_storage
}

warn_docker_storage() {
  local data_root="" daemon="/etc/docker/daemon.json"
  log "Docker overlay data-root stays on container-local disk (/var/lib/docker). ${ROOT}/images is a shared image cache. Overlay layers do not work on the shared mount ${WORK_MOUNT}."
  if [[ -f "$daemon" ]] && command -v python3 >/dev/null 2>&1; then
    data_root="$(python3 -c 'import json,sys
try:
    data=json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    data={}
print(data.get("data-root") or "")' "$daemon" 2>/dev/null || true)"
  fi
  if [[ -z "$data_root" ]]; then
    data_root="${DOCKER_DATA_ROOT:-}"
  fi
  if [[ -n "$data_root" ]] && path_on_work "$data_root"; then
    log "WARNING: configured Docker data-root ${data_root} is on ${WORK_MOUNT}. Use /var/lib/docker for the runtime and ${ROOT}/images for a shared image cache."
  fi
}

# The adapter's path inside a release bundle, resolved under the release
# this boot runs (TARGET_RELEASE). An adapter computes GOLIATH_BUNDLED_START
# before ensure_current; when that was the previous release (current pointed
# at 2026.10.15 while this boot promoted 2026.10.17) its path is stale, so
# only the part after runtime-bundle/ is kept and re-rooted.
target_bundled_start() {
  local bundled="${GOLIATH_BUNDLED_START:-}" rel="${GOLIATH_BUNDLED_START_REL:-}"
  if [[ -z "$rel" && "$bundled" == */runtime-bundle/* ]]; then
    rel="${bundled##*/runtime-bundle/}"
  fi
  if [[ -n "$rel" && -n "$TARGET_RELEASE" ]]; then
    printf '%s/runtime-bundle/%s' "$TARGET_RELEASE" "$rel"
    return 0
  fi
  printf '%s' "$bundled"
}

maybe_reexec() {
  # A provider adapter sets GOLIATH_BUNDLED_START to its copy inside the
  # release bundle. After current exists, switch to the copy in the release
  # this boot runs (explicit path, not $ROOT/current) once.
  [[ "${GOLIATH_START_REEXECED:-}" == "1" ]] && return 0
  local bundled
  bundled="$(target_bundled_start)"
  [[ -n "$bundled" && -f "$bundled" ]] || return 0
  [[ -n "$TARGET_RELEASE" ]] && export GOLIATH_RELEASE_DIR="$TARGET_RELEASE"
  local self target
  self="$(readlink -f "${GOLIATH_ADAPTER_SCRIPT:-$0}" 2>/dev/null || printf '%s' "${GOLIATH_ADAPTER_SCRIPT:-$0}")"
  target="$(readlink -f "$bundled" 2>/dev/null || printf '%s' "$bundled")"
  [[ "$self" == "$target" ]] && return 0
  log "reexec bundled start adapter ${target}"
  export GOLIATH_START_REEXECED=1
  exec bash "$target" "$@"
}

detect_gpu() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    die "nvidia-smi is not installed; goliath-worker requires a GPU"
  fi
  local raw
  raw="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || true)"
  raw="${raw%%$'\n'*}"
  raw="$(printf '%s' "$raw" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [[ -z "$raw" ]]; then
    die "nvidia-smi did not report a GPU; goliath-worker requires a GPU (RTX 6000 Ada)"
  fi
  GPU_DESC="$raw"
  log "GPU detected via nvidia-smi: ${GPU_DESC}"
}

append_kv() {
  local dest="$1"
  local key="$2"
  local val="$3"
  [[ -n "$val" ]] || return 0
  val="${val//\\/\\\\}"
  val="${val//\"/\\\"}"
  val="${val//$'\n'/}"
  printf '%s="%s"\n' "$key" "$val" >>"$dest"
}

system_ca_bundle() {
  local candidate
  if [[ -n "${GOLIATH_SYSTEM_CA_BUNDLE:-}" && -f "${GOLIATH_SYSTEM_CA_BUNDLE}" ]]; then
    printf '%s' "${GOLIATH_SYSTEM_CA_BUNDLE}"
    return 0
  fi
  for candidate in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
    if [[ -f "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

write_gateway_ca_pem() {
  local dest="$1"
  local pem="$2"
  local old_umask
  if [[ "$pem" != *$'\n'* && "$pem" == *'\n'* ]]; then
    pem="${pem//\\n/$'\n'}"
  fi
  if [[ "$pem" != *"BEGIN CERTIFICATE"* ]]; then
    die "GOLIATH_GATEWAY_CA_PEM does not contain a PEM certificate"
  fi
  mkdir -p "$(dirname "$dest")"
  old_umask="$(umask)"
  apply_work_umask
  printf '%s' "$pem" >"$dest"
  if [[ "$pem" != *$'\n' ]]; then
    printf '\n' >>"$dest"
  fi
  umask "$old_umask"
  chmod_work "$(work_file_mode)" "$dest"
}

# Persist the gateway trust anchor and point requests/OpenSSL at a bundle that
# still contains the system CAs. GOLIATH_GATEWAY_CA_PEM is the PEM text from
# the environment. It is removed from the environment after the file is written.
install_gateway_ca() {
  local pem dest system bundle
  pem="${GOLIATH_GATEWAY_CA_PEM:-}"
  dest="${GOLIATH_GATEWAY_CA_FILE:-$ROOT/env/gateway-ca.pem}"
  if [[ -n "$pem" ]]; then
    write_gateway_ca_pem "$dest" "$pem"
    unset GOLIATH_GATEWAY_CA_PEM
    export GOLIATH_GATEWAY_CA_FILE="$dest"
  elif [[ -n "${GOLIATH_GATEWAY_CA_FILE:-}" && -f "$GOLIATH_GATEWAY_CA_FILE" ]]; then
    dest="$GOLIATH_GATEWAY_CA_FILE"
  elif [[ -f "$ROOT/env/gateway-ca.pem" ]]; then
    dest="$ROOT/env/gateway-ca.pem"
    export GOLIATH_GATEWAY_CA_FILE="$dest"
  else
    return 0
  fi
  export GOLIATH_GATEWAY_CA_FILE="$dest"
  bundle="$ROOT/env/requests-ca-bundle.pem"
  system="$(system_ca_bundle || true)"
  if [[ -n "$system" ]]; then
    {
      cat "$system"
      [[ "$(tail -c 1 "$system" 2>/dev/null || true)" == $'\n' ]] || printf '\n'
      cat "$dest"
    } >"$bundle"
    chmod_work "$(work_file_mode)" "$bundle"
    if [[ -z "${REQUESTS_CA_BUNDLE:-}" ]]; then
      export REQUESTS_CA_BUNDLE="$bundle"
    fi
    if [[ -z "${SSL_CERT_FILE:-}" ]]; then
      export SSL_CERT_FILE="$bundle"
    fi
    log "gateway CA file ${dest}; requests bundle ${REQUESTS_CA_BUNDLE}"
  else
    log "gateway CA file ${dest}; system CA bundle not found, so REQUESTS_CA_BUNDLE was left unset"
  fi
}

secret_env_key() {
  case "$1" in
    *PASSWORD*|*SECRET*|*TOKEN*|*ACCESS_KEY*|*PRIVATE_KEY*|*CREDENTIAL*) return 0 ;;
    *) return 1 ;;
  esac
}

# Older files used AWS_* and GOLIATH_LONGTERM_* for both stores. Drop them
# so one store's settings are not reused under the other store's names.
storage_alias_key() {
  case "$1" in
    AWS_*|GOLIATH_LONGTERM_*|GOLIATH_SHARED_PROVIDER|GOLIATH_SHARED_BUCKET) return 0 ;;
    *) return 1 ;;
  esac
}

write_worker_env() {
  local shared="$ROOT/env/worker.env"
  local dest pod_env secrets old_umask tmp owned line key
  pod_env="$(node_root)/env/worker.env"
  secrets="$(private_dir)/secrets.env"
  dest="$pod_env"
  local b2_key="${B2_ACCESS_KEY_ID:-}"
  local b2_secret="${B2_SECRET_ACCESS_KEY:-}"
  local b2_endpoint="${B2_ENDPOINT:-}"
  local b2_bucket="${B2_BUCKET:-}"
  local long_key="${LONGTERM_ACCESS_KEY_ID:-}"
  local long_secret="${LONGTERM_SECRET_ACCESS_KEY:-}"
  local long_endpoint="${LONGTERM_ENDPOINT:-}"
  local long_bucket="${LONGTERM_BUCKET:-}"
  local long_host="${LONGTERM_HOST:-}"
  local long_share="${LONGTERM_SHARE:-}"
  # promote_release.sh already wrote Parabricks, PATH, CAAS, and HBM
  # settings. Refresh the storage and API keys this script owns, plus the
  # extractor paths when this pod installed MethylExtractor locally.
  owned='WORKER_API_BASE|GOLIATH_ENV|B2_BUCKET|B2_ENDPOINT|LONGTERM_BUCKET|LONGTERM_ENDPOINT|LONGTERM_HOST|LONGTERM_SHARE|WORKER_CAPABILITY|POSTGRES_HOST|POSTGRES_DB|POSTGRES_USER|POSTGRES_SSLMODE|POSTGRES_CHANNEL_BINDING|POSTGRES_PASSWORD|GOLIATH_GATEWAY_CA_FILE|REQUESTS_CA_BUNDLE|SSL_CERT_FILE'
  local have_local_venv=0
  if [[ "$LOCAL_VENV_READY" -eq 1 && -n "${GOLIATH_WORKER_VENV:-}" ]]; then
    have_local_venv=1
    owned="PATH|GOLIATH_WORKER_VENV|${owned}"
  fi
  local have_local_extractor=0
  if [[ -n "${METHYL_EXTRACTOR_HOME:-}" && -x "${METHYL_EXTRACTOR_BIN:-}" ]]; then
    have_local_extractor=1
    owned="METHYL_EXTRACTOR_HOME|METHYL_EXTRACTOR_BIN|HDF5_PLUGIN_PATH|LD_LIBRARY_PATH|${owned}"
  fi
  mkdir -p "$(dirname "$pod_env")" "$ROOT/env"
  old_umask="$(umask)"
  apply_work_umask
  tmp="$(mktemp "${pod_env}.XXXXXX")"
  if [[ -f "$shared" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      case "$line" in
        ''|\#*) printf '%s\n' "$line" >>"$tmp"; continue ;;
      esac
      key="${line%%=*}"
      if secret_env_key "$key" || storage_alias_key "$key"; then
        continue
      fi
      if [[ "$key" =~ ^(${owned})$ ]]; then
        continue
      fi
      printf '%s\n' "$line" >>"$tmp"
    done <"$shared"
  fi
  if [[ -s "$tmp" && "$(tail -c 1 "$tmp" || true)" != $'\n' ]]; then
    printf '\n' >>"$tmp"
  fi
  if [[ "$have_local_extractor" -eq 1 ]]; then
    append_kv "$tmp" METHYL_EXTRACTOR_HOME "$METHYL_EXTRACTOR_HOME"
    append_kv "$tmp" METHYL_EXTRACTOR_BIN "$METHYL_EXTRACTOR_BIN"
    append_kv "$tmp" HDF5_PLUGIN_PATH "$HDF5_PLUGIN_PATH"
    # libhts.so.3 ships in the tarball's lib/; worker.env is literal.
    append_kv "$tmp" LD_LIBRARY_PATH "${LD_LIBRARY_PATH:-$METHYL_EXTRACTOR_HOME/lib}"
  fi
  if [[ "$have_local_venv" -eq 1 ]]; then
    # This pod's venv may have fallen back to /tmp; worker.env is literal.
    local venv_path="$GOLIATH_WORKER_VENV/bin"
    if [[ "$have_local_extractor" -eq 1 ]]; then
      venv_path="$venv_path:$METHYL_EXTRACTOR_HOME/bin"
    fi
    append_kv "$tmp" GOLIATH_WORKER_VENV "$GOLIATH_WORKER_VENV"
    append_kv "$tmp" PATH "$venv_path:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  fi
  append_kv "$tmp" WORKER_API_BASE "${WORKER_API_BASE:-}"
  append_kv "$tmp" GOLIATH_ENV "$GOLIATH_ENV"
  append_kv "$tmp" GOLIATH_GATEWAY_CA_FILE "${GOLIATH_GATEWAY_CA_FILE:-}"
  append_kv "$tmp" REQUESTS_CA_BUNDLE "${REQUESTS_CA_BUNDLE:-}"
  append_kv "$tmp" SSL_CERT_FILE "${SSL_CERT_FILE:-}"
  append_kv "$tmp" B2_BUCKET "$b2_bucket"
  append_kv "$tmp" B2_ENDPOINT "$b2_endpoint"
  append_kv "$tmp" DOCKER_DATA_ROOT "/var/lib/docker"
  append_kv "$tmp" GOLIATH_IMAGE_CACHE "$ROOT/images"
  append_kv "$tmp" LONGTERM_BUCKET "$long_bucket"
  append_kv "$tmp" LONGTERM_ENDPOINT "$long_endpoint"
  append_kv "$tmp" LONGTERM_HOST "$long_host"
  append_kv "$tmp" LONGTERM_SHARE "$long_share"
  append_kv "$tmp" GOLIATH_NODE_ID "$(node_id)"
  append_kv "$tmp" GOLIATH_SHARED_GROUP "${GOLIATH_SHARED_GROUP:-}"
  if [[ -n "${GOLIATH_WORKER_CAPABILITY:-}" ]]; then
    append_kv "$tmp" WORKER_CAPABILITY "$GOLIATH_WORKER_CAPABILITY"
  fi
  # The worker talks to the gateway. The database password stays off this
  # node unless an operator sets GOLIATH_WORKER_NEEDS_DATABASE=1.
  if [[ "${GOLIATH_WORKER_NEEDS_DATABASE:-}" == "1" ]]; then
    append_kv "$tmp" POSTGRES_HOST "${POSTGRES_HOST:-}"
    append_kv "$tmp" POSTGRES_DB "${POSTGRES_DB:-}"
    append_kv "$tmp" POSTGRES_USER "${POSTGRES_USER:-}"
    append_kv "$tmp" POSTGRES_SSLMODE "${POSTGRES_SSLMODE:-require}"
    append_kv "$tmp" POSTGRES_CHANNEL_BINDING "${POSTGRES_CHANNEL_BINDING:-require}"
  fi
  mv -f "$tmp" "$pod_env"
  umask "$old_umask"
  chmod_work "$(work_file_mode)" "$pod_env"
  if [[ -f "$shared" ]]; then
    local stripped
    apply_work_umask
    stripped="$(mktemp "${shared}.XXXXXX")"
    while IFS= read -r line || [[ -n "$line" ]]; do
      case "$line" in
        ''|\#*) printf '%s\n' "$line" >>"$stripped"; continue ;;
      esac
      key="${line%%=*}"
      if secret_env_key "$key" || storage_alias_key "$key"; then
        continue
      fi
      printf '%s\n' "$line" >>"$stripped"
    done <"$shared"
    mv -f "$stripped" "$shared"
    umask "$old_umask"
    chmod_work "$(work_file_mode)" "$shared"
  fi
  umask 077
  tmp="$(mktemp "${secrets}.XXXXXX")"
  append_kv "$tmp" B2_ACCESS_KEY_ID "$b2_key"
  append_kv "$tmp" B2_SECRET_ACCESS_KEY "$b2_secret"
  append_kv "$tmp" LONGTERM_ACCESS_KEY_ID "$long_key"
  append_kv "$tmp" LONGTERM_SECRET_ACCESS_KEY "$long_secret"
  if [[ "${GOLIATH_WORKER_NEEDS_DATABASE:-}" == "1" ]]; then
    append_kv "$tmp" POSTGRES_PASSWORD "${NEON_TEST_PASSWORD:-${POSTGRES_PASSWORD:-}}"
  fi
  mv -f "$tmp" "$secrets"
  umask "$old_umask"
  if ! chmod 600 "$secrets"; then
    die "cannot protect ${secrets}"
  fi
  log "wrote ${pod_env} and secrets ${secrets}"
}

write_token_file() {
  local dest="$1"
  local value="$2"
  local old_umask
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    die "GOLIATH_WORKER_TOKEN contains a newline"
  fi
  if path_on_work "$dest"; then
    die "refusing to write the worker token on ${WORK_MOUNT}"
  fi
  mkdir -p "$(dirname "$dest")"
  old_umask="$(umask)"
  umask 077
  case "$value" in
    WORKER_ID=*|WORKER_TOKEN=*)
      printf '%s\n' "$value" >"$dest"
      ;;
    *)
      if [[ -n "${WORKER_ID:-}" ]]; then
        printf 'WORKER_ID=%s\nWORKER_TOKEN=%s\n' "$WORKER_ID" "$value" >"$dest"
      else
        printf 'WORKER_TOKEN=%s\n' "$value" >"$dest"
      fi
      ;;
  esac
  umask "$old_umask"
  if ! chmod 600 "$dest"; then
    die "cannot protect ${dest}"
  fi
}

restore_or_write_token() {
  local dest="$METHYL_WORKER_TOKEN_FILE"
  if path_on_work "$dest"; then
    die "refusing to write the worker token on ${WORK_MOUNT}"
  fi
  if [[ -f "$dest" ]]; then
    log "using worker token ${dest}"
    return 0
  fi
  if [[ -z "${GOLIATH_WORKER_TOKEN:-}" ]]; then
    log "no worker token yet; enroll will write ${dest}"
    return 0
  fi
  write_token_file "$dest" "$GOLIATH_WORKER_TOKEN"
  log "wrote worker token to ${dest}"
}

persist_token() {
  local dest="$METHYL_WORKER_TOKEN_FILE"
  [[ -f "$dest" ]] || die "worker token file ${dest} was not written"
  if path_on_work "$dest"; then
    die "worker token ${dest} is on ${WORK_MOUNT}"
  fi
}

stamp_payload() {
  local current
  current="$(cur_dir)"
  printf 'api=%s\narch=%s\ncurrent=%s\n' "$WORKER_API_BASE" "$ARCH" "$current"
}

already_joined() {
  local stamp="$1"
  local token="$2"
  local expected
  [[ -f "$stamp" && -f "$token" ]] || return 1
  expected="$(stamp_payload)"
  [[ "$(cat "$stamp")" == "$expected" ]] || return 1
  grep -q '^WORKER_ID=' "$token" || return 1
  grep -q '^WORKER_TOKEN=' "$token" || return 1
}

run_provision() {
  local provision
  provision="$(resolve_tool "${GOLIATH_PROVISION_SCRIPT:-}" provision_worker_node.sh)" \
    || die "provision_worker_node.sh not found under $ROOT/current"
  local -a args=(
    --join-mode join
    --arch "$ARCH"
    --root "$ROOT"
    --cluster "${CLUSTER_KEY:-goliath}"
    --gpu
    --enroll-worker
    --skip-preflight
    --skip-host-setup
    --skip-verify
  )
  if [[ -n "${GOLIATH_WORKER_CAPABILITY:-}" ]]; then
    args+=(--capability "$GOLIATH_WORKER_CAPABILITY")
  fi
  log "provision_worker_node.sh --join-mode join --arch ${ARCH} --gpu (nvidia-smi probe; catalog capability only if GOLIATH_WORKER_CAPABILITY is set)"
  bash "$provision" "${args[@]}"
}

report_worker() {
  local action="$1"
  local reporter msg
  msg="node=$(node_id) host=${HOST_SHORT} arch=${ARCH} GPU=${GPU_DESC}"
  reporter="$(resolve_tool "${GOLIATH_REPORT_SCRIPT:-}" report_deploy.sh)" \
    || die "report_deploy.sh not found under $ROOT/current"
  bash "$reporter" "$action" \
    --service goliath-worker \
    --env "$GOLIATH_ENV" \
    --message "$msg" || true
}

systemd_running() {
  command -v systemctl >/dev/null 2>&1 || return 1
  [[ -d /run/systemd/system ]] || return 1
  systemctl show-environment >/dev/null 2>&1
}

worker_bin() {
  local candidate
  # Only the pod-local venv: nothing under $ROOT on the volume can execute.
  if [[ -n "${GOLIATH_WORKER_VENV:-}" ]]; then
    candidate="$GOLIATH_WORKER_VENV/bin/methyl-worker"
    if [[ -x "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  fi
  if command -v methyl-worker >/dev/null 2>&1; then
    command -v methyl-worker
    return 0
  fi
  return 1
}

load_token_env() {
  local file="$1"
  local line key val
  [[ -f "$file" ]] || die "missing worker token file ${file}"
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    case "$key" in
      WORKER_ID) WORKER_ID="$val"; export WORKER_ID ;;
      WORKER_TOKEN) WORKER_TOKEN="$val"; export WORKER_TOKEN ;;
      *) ;;
    esac
  done <"$file"
}

# Create the configured temp directories on local disk before anything calls
# mktemp. worker.env sets TMPDIR=/var/tmp/methyl-samtools (samtools sort
# scratch); a fresh container does not have it and mktemp then fails. A
# directory that cannot be created makes TMPDIR fall back to /tmp.
ensure_tmpdirs() {
  local d envfile cfg=""
  for envfile in "${ROOT:-/work/goliath}/env/worker.env" "${GOLIATH_POD_ENV_FILE:-}"; do
    [[ -n "$envfile" && -f "$envfile" ]] || continue
    cfg="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}TMPDIR=//p' "$envfile" 2>/dev/null | tail -n 1 | tr -d '"'"'"'')"
    [[ -n "$cfg" ]] && break
  done
  for d in "${TMPDIR:-}" "$cfg" "${METHYL_SAMTOOLS_TMPDIR:-/var/tmp/methyl-samtools}"; do
    [[ -n "$d" && "$d" == /* ]] || continue
    if mkdir -p "$d" 2>/dev/null && [[ -w "$d" ]]; then
      chmod 1777 "$d" 2>/dev/null || true
    else
      echo "[WARN] cannot create temp dir ${d}" >&2
      if [[ "$d" == "${TMPDIR:-}" ]]; then
        echo "[WARN] TMPDIR=${d} is unusable; using /tmp" >&2
        TMPDIR=/tmp
        export TMPDIR
      fi
    fi
  done
}

# Local log directory. GOLIATH_LOG_DIR may override it, but a path on the
# work mount is refused (logs never go to /work). /var/log/goliath when it
# can be created, else /tmp/goliath-logs.
resolve_log_dir() {
  local want="${GOLIATH_LOG_DIR:-/var/log/goliath}" d
  if path_on_work "$want"; then
    log "warning: GOLIATH_LOG_DIR=${want} is on ${WORK_MOUNT}; runtime logs stay on local disk"
    want="/var/log/goliath"
  fi
  for d in "$want" /var/log/goliath "${TMPDIR:-/tmp}/goliath-logs" /tmp/goliath-logs; do
    path_on_work "$d" && continue
    if mkdir -p "$d" 2>/dev/null && [[ -w "$d" ]]; then
      printf '%s' "$d"
      return 0
    fi
  done
  return 1
}

# Append stdin to a log file, rotating by size: when the file exceeds
# max_bytes it becomes file.1 (file.1 -> file.2 ... up to keep). The size is
# checked every 100 lines, so a file overshoots the cap by at most that much.
rotating_log() {
  local file="$1" max="${2:-52428800}" keep="${3:-3}" line n=0 i size
  [[ "$max" =~ ^[0-9]+$ && "$max" -gt 0 ]] || max=52428800
  [[ "$keep" =~ ^[0-9]+$ && "$keep" -ge 1 ]] || keep=3
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    if (( n % 100 == 1 )) && [[ -f "$file" ]]; then
      size="$(stat -c %s "$file" 2>/dev/null || echo 0)"
      if (( size > max )); then
        for ((i = keep - 1; i >= 1; i--)); do
          [[ -f "$file.$i" ]] && mv -f "$file.$i" "$file.$((i + 1))"
        done
        mv -f "$file" "$file.1"
      fi
    fi
    printf '%s\n' "$line" >>"$file"
  done
}

start_supervisor() {
  local bin pidfile token_file
  bin="$(worker_bin)" || {
    log "methyl-worker not found under ${GOLIATH_WORKER_VENV:-the local venv} or on PATH"
    return 1
  }
  mkdir -p "$LOG_DIR"
  # worker.env sets TMPDIR=/var/tmp/methyl-samtools. The systemd unit creates
  # that directory in ExecStartPre. Without systemd, tempfile calls fail on
  # the first job unless the supervisor creates it too.
  ensure_tmpdirs
  pidfile="$LOG_DIR/methyl-worker.supervisor.pid"
  if [[ -f "$pidfile" ]]; then
    local old
    old="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
      log "methyl-worker supervisor already running pid=${old}"
      return 0
    fi
  fi
  token_file="$METHYL_WORKER_TOKEN_FILE"
  # Monitor mode puts this background loop in its own process group so a
  # later stop (kill -- -pid) also stops methyl-worker.
  set -m
  (
    # Load the same worker.env EnvironmentFile the unit would, then the
    # token so WORKER_ID / WORKER_TOKEN win.
    local env_pod secrets_env
    env_pod="$(node_root)/env/worker.env"
    secrets_env="$(private_dir)/secrets.env"
    if [[ -f "$ROOT/env/worker.env" ]]; then
      set -a
      # shellcheck disable=SC1091
      source "$ROOT/env/worker.env"
      set +a
    fi
    if [[ -f "$env_pod" ]]; then
      set -a
      # shellcheck disable=SC1090,SC1091
      source "$env_pod"
      set +a
    fi
    if [[ -f "$secrets_env" ]]; then
      set -a
      # shellcheck disable=SC1090,SC1091
      source "$secrets_env"
      set +a
    fi
    load_token_env "$token_file"
    while true; do
      "$bin" --api-base "$WORKER_API_BASE" 2>&1 \
        | rotating_log "$LOG_DIR/methyl-worker.log" "$GOLIATH_LOG_MAX_BYTES" "$GOLIATH_LOG_KEEP" || true
      sleep 5
    done
  ) &
  set +m
  printf '%s\n' "$!" >"$pidfile"
  log "methyl-worker supervisor pid=$(cat "$pidfile") logs=${LOG_DIR}/methyl-worker.log"
}

start_worker() {
  local installer=""
  if systemd_running; then
    installer="$(resolve_tool "" install_worker_systemd.sh)" || installer=""
    if [[ -n "$installer" ]]; then
      local -a sd_args=(--root "$ROOT" --arch "$ARCH")
      if [[ -n "${GOLIATH_WORKER_CAPABILITY:-}" ]]; then
        sd_args+=(--capability "$GOLIATH_WORKER_CAPABILITY")
      else
        sd_args+=(--detect-capabilities)
      fi
      if GOLIATH_ROLLOUTS_EXTERNAL=1 bash "$installer" "${sd_args[@]}"; then
        log "started methyl-worker via systemd"
        return 0
      fi
      log "systemd unit install failed; falling back to a supervisor"
    else
      log "install_worker_systemd.sh not found; falling back to a supervisor"
    fi
  else
    log "systemd is not running; starting methyl-worker under a supervisor"
  fi
  start_supervisor
}

finish_exec() {
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    log "GOLIATH_START_NO_EXEC set; not execing a keepalive"
    exit 0
  fi
  if [[ -z "${GOLIATH_START_KEEPALIVE:-}" ]]; then
    log "worker is running; no keepalive command set"
    exit 0
  fi
  printf '[start-shared-worker] keeping the process alive\n' >&2
  exec_keepalive "$@" || exit 1
}

main() {
  ensure_tmpdirs
  wait_for_work
  export GOLIATH_SHARED_WORK=1
  GOLIATH_NODE_ID="$(node_id)"
  export GOLIATH_NODE_ID
  apply_work_umask
  # /work/goliath/nodes/<id> keeps only small per-node state that the next
  # boot reads back: status/joined (skip re-provision), env/worker.env (the
  # supervisor's EnvironmentFile), tmp/ (release download staging). Logs
  # are local (resolve_log_dir).
  mkdir -p "$ROOT" "$(node_root)/tmp" "$(node_root)/status"
  chmod_work "$(work_dir_mode)" "$ROOT" "$ROOT/nodes" "$(node_root)" "$(node_root)/tmp" "$(node_root)/status"
  LOG_DIR="$(resolve_log_dir)" || die "no writable local log directory (tried /var/log/goliath and /tmp/goliath-logs)"
  log "runtime logs on local disk: ${LOG_DIR}"
  ensure_current
  maybe_reexec "$@"
  ensure_system_deps
  ensure_work_layout
  install_local_extractor
  install_local_venv
  [[ -n "${WORKER_API_BASE:-}" ]] || die "WORKER_API_BASE is required (profile public_base_url, for example https://45.76.21.8/v1)"
  detect_gpu
  install_gateway_ca
  HOST_SHORT="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)"
  restore_or_write_token
  write_worker_env
  # worker.env (just written) names TMPDIR; provision sources it and mktemps.
  ensure_tmpdirs
  local state stamp
  state="$(node_root)/status"
  stamp="$state/joined"
  mkdir -p "$state"
  if already_joined "$stamp" "$METHYL_WORKER_TOKEN_FILE"; then
    log "already joined this release; skipping provision_worker_node.sh"
  else
    run_provision
    persist_token
    apply_work_umask
    stamp_payload >"$stamp"
    chmod_work "$(work_file_mode)" "$stamp"
  fi
  report_worker start
  if ! start_worker; then
    report_worker failed
    die "worker failed to start"
  fi
  report_worker succeeded
  finish_exec "$@"
}

main "$@"
