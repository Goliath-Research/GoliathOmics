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
CHMOD_WARNED=0

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
      [[ "$CHMOD_WARNED" == 1 ]] || log "note: chmod ${mode} ${path} was not applied (expected on a shared volume that fixes modes); continuing, further notices suppressed"
      CHMOD_WARNED=1
    fi
    if shared_group; then
      if ! chgrp "$group" "$path" 2>/dev/null; then
        [[ "$CHMOD_WARNED" == 1 ]] || log "note: chgrp ${group} ${path} was not applied (expected on a shared volume); continuing, further notices suppressed"
        CHMOD_WARNED=1
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

# Install a script atomically: copy to a per-process temp file next to the
# destination (same filesystem), set the mode with the warn-and-continue
# helper, then rename over the destination. Plain cp (no -p/-a) so no owner,
# mode or mtime is preserved on volumes that reject chown/chmod/utime. Pods
# booting at once each use their own temp file; the last rename wins.
copy_script() {
  local src="$1"
  local dest="$2"
  local tmp
  tmp="$(dirname "$dest")/.$(basename "$dest").tmp.$$"
  rm -f "$tmp"
  cp -f "$src" "$tmp" || { rm -f "$tmp"; hold "cannot write ${tmp}"; }
  chmod_work 755 "$tmp"
  mv -f "$tmp" "$dest" || { rm -f "$tmp"; hold "cannot install ${dest}"; }
}

# Release tag the scripts in $ROOT/bin came from. Written last, atomically.
release_marker() {
  printf '%s' "$ROOT/bin/.release"
}

installed_release() {
  local marker
  marker="$(release_marker)"
  [[ -f "$marker" ]] || return 0
  head -n 1 "$marker" 2>/dev/null | tr -d '[:space:]' || true
}

record_release() {
  local tag="$1"
  local marker tmp
  [[ -n "$tag" ]] || return 0
  marker="$(release_marker)"
  tmp="${marker}.tmp.$$"
  printf '%s\n' "$tag" >"$tmp" || { rm -f "$tmp"; log "warning: cannot write ${tmp}; continuing"; return 0; }
  chmod_work 644 "$tmp"
  mv -f "$tmp" "$marker" || { rm -f "$tmp"; log "warning: cannot record ${marker}; continuing"; return 0; }
  log "recorded release ${tag} in ${marker}"
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

# Release whose bundle the scripts come from: the wanted release's own
# directory when it is already on the volume (explicit path), else the
# release behind $ROOT/current. Never trust current alone: during a promote
# by another pod it can still name the previous release.
bundle_dir() {
  local ver="${WANTED_TAG#goliathomics-}"
  if [[ -n "$WANTED_TAG" && "$ver" =~ ^[A-Za-z0-9._+-]+$ && -f "$ROOT/releases/$ver/manifest.json" ]]; then
    printf '%s\n' "$ROOT/releases/$ver"
    return 0
  fi
  cur_dir
}

# Tag matching bundle_dir, from its manifest version (goliathomics-<ver>).
bundle_release() {
  local manifest
  manifest="$(bundle_dir)/manifest.json"
  [[ -f "$manifest" ]] || return 0
  python3 -c 'import json,sys
v=json.load(open(sys.argv[1], encoding="utf-8")).get("version") or ""
if v:
    print(v if v.startswith("goliathomics-") else "goliathomics-" + v)' "$manifest" 2>/dev/null || true
}

# WANTED_TAG is the release the scripts should come from. It is known when
# GOLIATH_RELEASE_TAG is set or a token can resolve the newest release.
# Unknown (no token, no pin) keeps the previous behaviour.
WANTED_TAG=""
STALE_SCRIPTS=0
resolve_wanted_tag() {
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  local repo="${GOLIATH_GITHUB_REPO:-Goliath-Research/GoliathOmics}"
  if [[ -n "${GOLIATH_RELEASE_TAG:-}" ]]; then
    RELEASE_TAG="$GOLIATH_RELEASE_TAG"
    require_tag "$RELEASE_TAG"
    WANTED_TAG="$RELEASE_TAG"
    return 0
  fi
  [[ -n "$token" ]] || return 0
  require_repo "$repo"
  if [[ -z "$HEADER_FILE" || ! -f "$HEADER_FILE" ]]; then
    prepare_github_header "$token"
  fi
  if resolve_tag "$repo"; then
    require_tag "$RELEASE_TAG"
    WANTED_TAG="$RELEASE_TAG"
  else
    RELEASE_TAG=""
    log "warning: could not resolve the latest goliathomics release; keeping scripts on the volume"
  fi
}

# Where to take a script from: "bundle", "existing" or "fetch".
script_source() {
  local bundled_ok="$1"
  local existing_ok="$2"
  if [[ -z "$WANTED_TAG" ]]; then
    if [[ "$bundled_ok" == 1 ]]; then echo bundle
    elif [[ "$existing_ok" == 1 ]]; then echo existing
    else echo fetch
    fi
    return 0
  fi
  if [[ "$bundled_ok" == 1 && "$(bundle_release)" == "$WANTED_TAG" ]]; then
    echo bundle
  elif [[ "$existing_ok" == 1 && "$(installed_release)" == "$WANTED_TAG" ]]; then
    echo existing
  else
    echo fetch
  fi
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
  META_FILE="$(private_dir)/github-releases.$$.json"
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
# The API lists releases by creation date, not version: an older tag can
# come first (2026.10.9 was picked over 2026.10.12). Take the highest version.
import re
def key(tag):
    return [int(p) if p.isdigit() else -1 for p in re.split(r"[.+-]", tag[len("goliathomics-"):])]
tags = [
    rel.get("tag_name") or ""
    for rel in raw
    if isinstance(rel, dict) and not rel.get("draft") and not rel.get("prerelease")
]
tags = [t for t in tags if t.startswith("goliathomics-")]
if tags:
    sys.stdout.write(max(tags, key=key))
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
  META_FILE="$(private_dir)/${name}.$$.json"
  DOWNLOAD_TMP="$(private_dir)/${name}.$$.download"
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
  local bundled
  bundled="$(bundle_dir)/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  local dest="$ROOT/bin/goliath-worker-start.sh"
  local b=0 e=0
  script_ok "$bundled" && b=1
  script_ok "$dest" && e=1
  case "$(script_source "$b" "$e")" in
    bundle)
      copy_script "$bundled" "$dest"
      log "installed ${dest} from the current release bundle" ;;
    existing)
      chmod_work 755 "$dest"
      log "using existing ${dest} (release $(installed_release))" ;;
    *)
      if [[ -z "${GH_TOKEN:-${GITHUB_TOKEN:-}}" && "$e" == 1 ]]; then
        STALE_SCRIPTS=1
        log "warning: no token to update ${dest}; using the existing copy"
      else
        fetch_start_script "$dest"
      fi ;;
  esac
  script_ok "$dest" || hold "${dest} is missing after install"
}

# The adapter execs these. A bundle that only has the start script (an older
# release) may omit them; a token download installs the matching tag.
install_companion() {
  local repo_path="$1"
  local marker="$2"
  local bundled
  bundled="$(bundle_dir)/runtime-bundle/${repo_path}"
  local dest
  dest="$ROOT/bin/$(basename "$repo_path")"
  local b=0 e=0
  [[ -f "$bundled" ]] && grep -q "$marker" "$bundled" && b=1
  [[ -f "$dest" ]] && grep -q "$marker" "$dest" && e=1
  case "$(script_source "$b" "$e")" in
    bundle)
      copy_script "$bundled" "$dest"
      log "installed ${dest} from the current release bundle"
      return 0 ;;
    existing)
      chmod_work 755 "$dest"
      log "using existing ${dest} (release $(installed_release))"
      return 0 ;;
  esac
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  if [[ -z "$token" ]]; then
    if [[ "$e" == 1 ]]; then
      STALE_SCRIPTS=1
      log "warning: no token to update ${dest}; using the existing copy"
    else
      log "${dest} is not on the volume"
    fi
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
CHMOD_WARNED=0
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
  resolve_wanted_tag
  install_start_script
  install_companion "scripts/start_shared_worker.sh" "start_shared_worker"
  install_companion "deploy/runpod/keepalive.sh" "goliath-runpod"
  if [[ -n "$WANTED_TAG" && "$STALE_SCRIPTS" == 0 && "$(installed_release)" != "$WANTED_TAG" ]]; then
    record_release "$WANTED_TAG"
  fi
  clear_github_header
  local wanted_dir
  wanted_dir="$(bundle_dir)"
  if [[ -n "$WANTED_TAG" && "$wanted_dir" == "$ROOT/releases/${WANTED_TAG#goliathomics-}" ]]; then
    export GOLIATH_RELEASE_DIR="$wanted_dir"
  fi
  run_start "$ROOT/bin/goliath-worker-start.sh" "$@"
}

main "$@"
