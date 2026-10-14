#!/usr/bin/env bash
# Fetch goliath-worker-start.sh onto an empty /work volume and run it.
#
# The RunPod template start command is bash -lc of this file.
# This file must not contain a single-quote character, because that command
# wraps the file in single quotes. Do not run it under set -x.
#
# GH_TOKEN (or GITHUB_TOKEN) is a fine-grained PAT with Contents read. It is
# written to a mode 600 header file and removed. It is never printed.
# GOLIATH_RELEASE_TAG pins the git tag. Unset, the newest non-draft
# goliathomics-* release is used.
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
HEADER_FILE=""
DOWNLOAD_TMP=""
META_FILE=""
RELEASE_TAG=""
child=0

log() { printf "[goliath-bootstrap] %s\n" "$*"; }

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
  META_FILE="$ROOT/tmp/github-releases.json"
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

fetch_start_script() {
  local dest="$1"
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  local repo="${GOLIATH_GITHUB_REPO:-Goliath-Research/GoliathOmics}"
  local old_umask url
  [[ -n "$token" ]] || hold "GH_TOKEN is unset and ${dest} is not on the volume"
  case "$token" in
    *"
"*) hold "GH_TOKEN contains a newline" ;;
  esac
  require_repo "$repo"
  command -v python3 >/dev/null 2>&1 || hold "python3 is required to read the GitHub response"
  command -v curl >/dev/null 2>&1 || hold "curl is required to download goliath-worker-start.sh"
  old_umask="$(umask)"
  umask 077
  HEADER_FILE="$ROOT/tmp/github.headers"
  printf "Authorization: Bearer %s\n" "$token" >"$HEADER_FILE"
  chmod 600 "$HEADER_FILE"
  resolve_tag "$repo" || hold "no goliathomics GitHub release; set GOLIATH_RELEASE_TAG"
  require_tag "$RELEASE_TAG"
  log "downloading goliath-worker-start.sh from ${repo} ref ${RELEASE_TAG}"
  url="https://api.github.com/repos/${repo}/contents/deploy/runpod/goliath-worker-start.sh?ref=${RELEASE_TAG}"
  META_FILE="$ROOT/tmp/goliath-worker-start.json"
  DOWNLOAD_TMP="$ROOT/tmp/goliath-worker-start.download"
  curl_auth "$url" "$META_FILE" "application/vnd.github+json" \
    || hold "could not download goliath-worker-start.sh at ${RELEASE_TAG}"
  decode_contents || hold "GitHub contents response did not include the start script"
  rm -f "$META_FILE" "$HEADER_FILE"
  META_FILE=""
  HEADER_FILE=""
  umask "$old_umask"
  script_ok "$DOWNLOAD_TMP" || hold "downloaded file is not goliath-worker-start.sh"
  install -m 755 "$DOWNLOAD_TMP" "$dest"
  rm -f "$DOWNLOAD_TMP"
  DOWNLOAD_TMP=""
  log "installed ${dest} from ${RELEASE_TAG}"
}

install_start_script() {
  local bundled="$ROOT/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  local dest="$ROOT/bin/goliath-worker-start.sh"
  if script_ok "$bundled"; then
    install -m 755 "$bundled" "$dest"
    log "installed ${dest} from the current release bundle"
  elif script_ok "$dest"; then
    chmod 755 "$dest" || hold "cannot chmod ${dest}"
    log "using existing ${dest}"
  else
    fetch_start_script "$dest"
  fi
  script_ok "$dest" || hold "${dest} is missing after install"
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
  install -d -m 755 "$ROOT" "$ROOT/bin" "$ROOT/tmp" "$ROOT/logs" "$ROOT/env" \
    || hold "cannot create ${ROOT}"
  install_start_script
  run_start "$ROOT/bin/goliath-worker-start.sh" "$@"
}

main "$@"
