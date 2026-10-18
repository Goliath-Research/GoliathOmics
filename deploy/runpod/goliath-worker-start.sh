#!/usr/bin/env bash
# RunPod adapter. goliath-worker-start
#
# Provider-specific pieces live here: the pod id RunPod injects, and the
# image keepalive that holds sshd and Jupyter after the worker is up.
# The worker itself is scripts/start_shared_worker.sh. Another cloud calls
# that script directly. See deploy/README.md.
#
# Do not run this under set -x.

set +x
set -euo pipefail

ROOT="${GOLIATH_ROOT:-/work/goliath}"
export GOLIATH_ROOT="$ROOT"

if [[ -z "${GOLIATH_NODE_ID:-}" && -n "${RUNPOD_POD_ID:-}" ]]; then
  export GOLIATH_NODE_ID="$RUNPOD_POD_ID"
fi

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
# GOLIATH_RELEASE_DIR is the explicit release the caller (bootstrap or a
# start re-exec) chose. It wins over $ROOT/current, which can be mid-flip or
# still name the previous release during a promote.
CURRENT_DIR=""
if [[ -n "${GOLIATH_RELEASE_DIR:-}" && -f "${GOLIATH_RELEASE_DIR}/manifest.json" ]]; then
  CURRENT_DIR="$GOLIATH_RELEASE_DIR"
else
  CURRENT_DIR="$(goliath_current_dir "$ROOT" 2>/dev/null || true)"
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GOLIATH_ADAPTER_SCRIPT="${GOLIATH_ADAPTER_SCRIPT:-$HERE/$(basename "${BASH_SOURCE[0]}")}"
# Path of this adapter inside a release bundle; start_shared_worker.sh joins
# it with the release it actually runs before re-exec'ing.
export GOLIATH_BUNDLED_START_REL="${GOLIATH_BUNDLED_START_REL:-deploy/runpod/goliath-worker-start.sh}"
if [[ -z "${GOLIATH_BUNDLED_START:-}" ]]; then
  export GOLIATH_BUNDLED_START="${CURRENT_DIR:-$ROOT/current}/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
fi

# Unset means "use the RunPod keepalive". A caller that sets the variable,
# including to another executable, keeps that command. GOLIATH_START_NO_EXEC
# is honored by the shared start and skips the keepalive entirely.
if [[ -z "${GOLIATH_START_KEEPALIVE:-}" ]]; then
  # $HERE is on the shared volume, which drops exec bits. The shared start
  # runs a *.sh keepalive with bash when it is not executable.
  if [[ -f "$HERE/keepalive.sh" ]]; then
    export GOLIATH_START_KEEPALIVE="$HERE/keepalive.sh"
  elif [[ -x /start.sh ]]; then
    export GOLIATH_START_KEEPALIVE=/start.sh
  fi
fi

# Release version in a release dir's manifest.json, or nothing.
release_version() {
  [[ -f "$1/manifest.json" ]] || return 0
  sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1/manifest.json" | head -n 1
}

# Release the bootstrap installed $ROOT/bin from ($ROOT/bin/.release holds
# goliathomics-<version>), or nothing.
bin_release_version() {
  local tag=""
  [[ -f "$ROOT/bin/.release" ]] && tag="$(head -n 1 "$ROOT/bin/.release" | tr -d '[:space:]')"
  tag="${tag#goliathomics-}"
  printf '%s' "${tag#v}"
}

# The shared start this boot runs. With no explicit GOLIATH_RELEASE_DIR,
# $ROOT/current still names the previous release on the first boot of a new
# one (it is promoted later by this very start). The bootstrap has already
# installed the wanted release's start into $ROOT/bin, so that copy wins
# when its release is newer than current's: running current's older start
# re-exec'd the previous release's adapter (worker-1 boot of 2026.10.18 ran
# releases/2026.10.17/.../goliath-worker-start.sh).
find_core() {
  local candidate cur_ver bin_ver bin_first=0
  if [[ -z "${GOLIATH_RELEASE_DIR:-}" || "$CURRENT_DIR" != "${GOLIATH_RELEASE_DIR:-}" ]]; then
    cur_ver="$(release_version "${CURRENT_DIR:-$ROOT/current}")"
    bin_ver="$(bin_release_version)"
    if [[ -n "$bin_ver" && -f "$ROOT/bin/start_shared_worker.sh" ]]; then
      if [[ -z "$cur_ver" ]] || { [[ "$bin_ver" != "$cur_ver" ]] \
          && [[ "$(printf '%s\n%s\n' "$cur_ver" "$bin_ver" | sort -V | tail -n 1)" == "$bin_ver" ]]; }; then
        bin_first=1
      fi
    fi
  fi
  for candidate in \
    "${GOLIATH_SHARED_WORKER_SCRIPT:-}" \
    "$([[ "$bin_first" == 1 ]] && printf '%s' "$ROOT/bin/start_shared_worker.sh")" \
    "${CURRENT_DIR:-$ROOT/current}/runtime-bundle/scripts/start_shared_worker.sh" \
    "$ROOT/bin/start_shared_worker.sh" \
    "$HERE/start_shared_worker.sh" \
    "$HERE/../../scripts/start_shared_worker.sh"
  do
    [[ -n "$candidate" && -f "$candidate" ]] || continue
    if grep -q "start_shared_worker" "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

core="$(find_core)" || {
  printf '[goliath-worker-start] ERROR: start_shared_worker.sh was not found next to this adapter or under %s\n' "$ROOT" >&2
  if [[ -n "${GOLIATH_START_KEEPALIVE:-}" && "${GOLIATH_START_NO_EXEC:-}" != "1" ]]; then
    printf '[goliath-worker-start] keeping the process alive\n' >&2
    if [[ -x "${GOLIATH_START_KEEPALIVE}" ]]; then
      exec "${GOLIATH_START_KEEPALIVE}"
    elif [[ -f "${GOLIATH_START_KEEPALIVE}" && "${GOLIATH_START_KEEPALIVE}" == *.sh ]]; then
      exec bash "${GOLIATH_START_KEEPALIVE}"
    fi
  fi
  exit 1
}

exec bash "$core" "$@"
