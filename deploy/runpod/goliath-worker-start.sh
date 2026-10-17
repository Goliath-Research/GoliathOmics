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

find_core() {
  local candidate
  for candidate in \
    "${GOLIATH_SHARED_WORKER_SCRIPT:-}" \
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
