#!/usr/bin/env bash
# Fetch goliath-worker-start.sh onto an empty /work volume and run it.
#
# The RunPod template start command is deploy/runpod/template-start-command.sh.
# That one line downloads this file from the goliathomics-* git tag through
# the GitHub contents API and runs it. This file is not pasted into the template.
# Do not run it under set -x.
#
# GH_TOKEN (or GITHUB_TOKEN) is a fine-grained PAT with Contents read. It is
# written to a mode 600 header file on container-local disk and removed. It is
# never printed and it is never stored on /work.
# GOLIATH_RELEASE_TAG pins the git tag. Unset, the newest non-draft
# goliathomics-* release is used.
# This file is the RunPod adapter. It installs the provider-neutral
# scripts/start_shared_worker.sh and deploy/runpod/goliath-worker-start.sh,
# then runs the adapter. /start.sh is the RunPod image keepalive; other
# clouds do not use this bootstrap. See deploy/README.md.
# On failure the pod stays up: /start.sh keeps sshd (port 22) and Jupyter
# (port 8888) running so RunPod does not restart the container.
# GOLIATH_START_NO_EXEC=1 makes failures exit 1 instead (tests).

set +x
set -euo pipefail
set -E

WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}"
ROOT="${GOLIATH_ROOT:-$WORK_MOUNT/goliath}"
WAIT_ATTEMPTS="${GOLIATH_WORK_WAIT_ATTEMPTS:-90}"
ORIG_UMASK="$(umask)"
PRIVATE_DIR=""
HEADER_FILE=""
DOWNLOAD_TMP=""
META_FILE=""
RELEASE_TAG=""
child=0

log() { printf "[goliath-bootstrap] %s\n" "$*"; }

path_on_work() {
  local path="$1"
  local mount="${WORK_MOUNT%/}"
  [[ "$path" == "$mount" || "$path" == "$mount"/* ]]
}

# Shared directories are 0755, or 2775 when GOLIATH_SHARED_GROUP exists.
# A filesystem that rejects chmod or chgrp (a Runpod global volume does)
# logs a warning. Secrets never use this helper.
shared_group() {
  local group="${GOLIATH_SHARED_GROUP:-}"
  [[ -n "$group" ]] || return 1
  getent group "$group" >/dev/null 2>&1
}

work_dir_mode() {
  if shared_group; then
    printf '2775'
  else
    printf '0755'
  fi
}

chmod_work() {
  local mode="$1"
  shift
  local path group="${GOLIATH_SHARED_GROUP:-}"
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    if ! chmod "$mode" "$path" 2>/dev/null; then
      log "warning: chmod ${mode} ${path} was not applied; continuing"
    fi
    if shared_group; then
      if ! chgrp "$group" "$path" 2>/dev/null; then
        log "warning: chgrp ${group} ${path} was not applied; continuing"
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
    hold "private dir ${base} must not be on ${WORK_MOUNT}"
  fi
  local old
  old="$(umask)"
  umask 077
  mkdir -p "$base"
  umask "$old"
  if ! chmod 700 "$base"; then
    hold "cannot protect ${base}"
  fi
  PRIVATE_DIR="$base"
  printf '%s' "$PRIVATE_DIR"
}

copy_script() {
  local src="$1"
  local dest="$2"
  cp -f "$src" "$dest"
  chmod_work 755 "$dest"
}

hold() {
  umask "$ORIG_UMASK"
  rm -f "$HEADER_FILE" "$DOWNLOAD_TMP" "$META_FILE"
  HEADER_FILE=""
  DOWNLOAD_TMP=""
  META_FILE=""
  printf "[goliath-bootstrap] ERROR: %s\n" "$*" >&2
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    exit 1
  fi
  printf "[goliath-bootstrap] keeping the pod alive for debugging (sshd port 22, Jupyter port 8888)\n" >&2
  local keepalive="${GOLIATH_START_KEEPALIVE:-/start.sh}"
  if [[ -x "$keepalive" ]]; then
    exec "$keepalive"
  fi
  printf "[goliath-bootstrap] %s is not executable; sleeping so the pod is not restarted\n" "$keepalive" >&2
  exec sleep infinity
}

on_stop() {
  if [[ "$child" != "0" ]]; then
    kill -TERM "$child" 2>/dev/null || true
  fi
  exit 0
}
on_err() {
  local rc=$?
  local line="$1"
  trap - ERR
  hold "command failed at line ${line} (exit ${rc})"
}
trap on_stop TERM INT
trap "on_err \"\${BASH_LINENO[0]}\"" ERR

wait_for_work() {
  local i
  for ((i = 1; i <= WAIT_ATTEMPTS; i++)); do
    if [[ -d "$WORK_MOUNT" && -w "$WORK_MOUNT" ]]; then
      return 0
    fi
    sleep 2
  done
  hold "persistent volume ${WORK_MOUNT} is not mounted and writable"
}

script_ok() {
  local path="$1"
  [[ -f "$path" && -s "$path" ]] || return 1
  head -n 1 "$path" | grep -q "bash" || return 1
  grep -q "goliath-worker-start" "$path"
}

require_repo() {
  local repo="$1"
  if [[ ! "$repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    hold "GOLIATH_GITHUB_REPO must look like owner/name"
  fi
}

require_tag() {
  local tag="$1"
  if [[ ! "$tag" =~ ^goliathomics-[A-Za-z0-9._+-]+$ ]]; then
    hold "release tag must look like goliathomics-<version>; set GOLIATH_RELEASE_TAG or publish a goliathomics release"
  fi
}

curl_auth() {
  local url="$1"
  local dest="$2"
  local accept="$3"
  curl -fsSL --retry 3 --retry-delay 2 \
    -H "@${HEADER_FILE}" \
    -H "Accept: ${accept}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -o "$dest" \
    "$url"
}

resolve_tag() {
  local repo="$1"
  RELEASE_TAG="${GOLIATH_RELEASE_TAG:-}"
  if [[ -n "$RELEASE_TAG" ]]; then
    return 0
  fi
  META_FILE="$(private_dir)/github-releases.json"
  local api="https://api.github.com/repos/${repo}/releases?per_page=100"
  curl_auth "$api" "$META_FILE" "application/vnd.github+json" \
    || hold "could not list GitHub releases"
  RELEASE_TAG="$(python3 - "$META_FILE" <<PY
import json, sys
raw = json.load(open(sys.argv[1], encoding="utf-8"))
if isinstance(raw, dict):
    raw = [raw]
if not isinstance(raw, list):
    sys.exit(0)
for rel in raw:
    if not isinstance(rel, dict):
        continue
    if rel.get("draft") or rel.get("prerelease"):
        continue
    name = rel.get("tag_name") or ""
    if name.startswith("goliathomics-"):
        sys.stdout.write(name)
        break
PY
)" || {
    rm -f "$META_FILE"
    META_FILE=""
    return 1
  }
  rm -f "$META_FILE"
  META_FILE=""
  [[ -n "$RELEASE_TAG" ]] || return 1
}

decode_contents() {
  python3 - "$META_FILE" >"$DOWNLOAD_TMP" <<PY
import base64, json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(doc, dict):
    sys.stderr.write("contents response was not an object\n")
    sys.exit(1)
encoding = doc.get("encoding") or ""
data = doc.get("content") or ""
if encoding != "base64" or not data:
    sys.stderr.write("contents response has no base64 content\n")
    sys.exit(1)
sys.stdout.buffer.write(base64.b64decode(data))
PY
}

prepare_github_header() {
  local token="$1"
  local old_umask
  [[ -n "$token" ]] || hold "GH_TOKEN is unset"
  case "$token" in
    *"
"*) hold "GH_TOKEN contains a newline" ;;
  esac
  command -v python3 >/dev/null 2>&1 || hold "python3 is required to read the GitHub response"
  command -v curl >/dev/null 2>&1 || hold "curl is required to download the worker scripts"
  old_umask="$(umask)"
  umask 077
  HEADER_FILE="$(private_dir)/github.headers"
  printf "Authorization: Bearer %s\n" "$token" >"$HEADER_FILE"
  umask "$old_umask"
  if ! chmod 600 "$HEADER_FILE"; then
    hold "cannot protect the GitHub auth header"
  fi
}

# Download one file from the release tag through the contents API.
# marker is a literal string that must appear in the file.
fetch_release_file() {
  local repo_path="$1"
  local dest="$2"
  local marker="$3"
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  local repo="${GOLIATH_GITHUB_REPO:-Goliath-Research/GoliathOmics}"
  local old_umask url name
  name="$(basename "$repo_path")"
  old_umask="$(umask)"
  [[ -n "$token" ]] || hold "GH_TOKEN is unset and ${dest} is not on the volume"
  require_repo "$repo"
  if [[ -z "$HEADER_FILE" || ! -f "$HEADER_FILE" ]]; then
    prepare_github_header "$token"
  fi
  if [[ -z "$RELEASE_TAG" ]]; then
    resolve_tag "$repo" || hold "no goliathomics GitHub release; set GOLIATH_RELEASE_TAG"
    require_tag "$RELEASE_TAG"
  fi
  log "downloading ${repo_path} from ${repo} ref ${RELEASE_TAG}"
  url="https://api.github.com/repos/${repo}/contents/${repo_path}?ref=${RELEASE_TAG}"
  META_FILE="$(private_dir)/${name}.json"
  DOWNLOAD_TMP="$(private_dir)/${name}.download"
  curl_auth "$url" "$META_FILE" "application/vnd.github+json" \
    || hold "could not download ${repo_path} at ${RELEASE_TAG}"
  decode_contents || hold "GitHub contents response did not include ${name}"
  rm -f "$META_FILE"
  META_FILE=""
  umask "$old_umask"
  grep -q "$marker" "$DOWNLOAD_TMP" || hold "downloaded file is not ${name}"
  copy_script "$DOWNLOAD_TMP" "$dest"
  rm -f "$DOWNLOAD_TMP"
  DOWNLOAD_TMP=""
  log "installed ${dest} from ${RELEASE_TAG}"
}

fetch_start_script() {
  fetch_release_file "deploy/runpod/goliath-worker-start.sh" "$1" "goliath-worker-start"
}

install_start_script() {
  local bundled="$ROOT/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  local dest="$ROOT/bin/goliath-worker-start.sh"
  if script_ok "$bundled"; then
    copy_script "$bundled" "$dest"
    log "installed ${dest} from the current release bundle"
  elif script_ok "$dest"; then
    chmod_work 755 "$dest"
    log "using existing ${dest}"
  else
    fetch_start_script "$dest"
  fi
  script_ok "$dest" || hold "${dest} is missing after install"
}

# The adapter execs these. A bundle that only has the start script (an older
# release) may omit them; a token download installs the matching tag.
install_companion() {
  local repo_path="$1"
  local marker="$2"
  local bundled="$ROOT/current/runtime-bundle/${repo_path}"
  local dest
  dest="$ROOT/bin/$(basename "$repo_path")"
  if [[ -f "$bundled" ]] && grep -q "$marker" "$bundled"; then
    copy_script "$bundled" "$dest"
    log "installed ${dest} from the current release bundle"
    return 0
  fi
  if [[ -f "$dest" ]] && grep -q "$marker" "$dest"; then
    chmod_work 755 "$dest"
    log "using existing ${dest}"
    return 0
  fi
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  if [[ -z "$token" ]]; then
    log "${dest} is not on the volume"
    return 0
  fi
  fetch_release_file "$repo_path" "$dest" "$marker"
}

clear_github_header() {
  if [[ -n "$HEADER_FILE" ]]; then
    rm -f "$HEADER_FILE"
    HEADER_FILE=""
  fi
}

run_start() {
  local dest="$1"
  local rc=0
  shift
  log "starting ${dest}"
  bash "$dest" "$@" &
  child=$!
  set +e
  wait "$child"
  rc=$?
  set -e
  child=0
  if [[ "$rc" -ne 0 ]]; then
    hold "goliath-worker-start.sh exited ${rc}"
  fi
  exit 0
}

main() {
  wait_for_work
  # mkdir, then chmod. A volume that rejects chmod (Runpod global volume)
  # keeps the umask mode. Scripts are started with bash, not the exec bit.
  if shared_group; then
    umask 002
  else
    umask 022
  fi
  mkdir -p "$ROOT" "$ROOT/bin" "$ROOT/releases" "$ROOT/nodes" \
    || hold "cannot create ${ROOT}"
  chmod_work "$(work_dir_mode)" "$ROOT" "$ROOT/bin" "$ROOT/releases" "$ROOT/nodes"
  install_start_script
  install_companion "scripts/start_shared_worker.sh" "start_shared_worker"
  install_companion "deploy/runpod/keepalive.sh" "goliath-runpod"
  clear_github_header
  run_start "$ROOT/bin/goliath-worker-start.sh" "$@"
}

main "$@"
