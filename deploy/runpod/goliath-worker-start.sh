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

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GOLIATH_ADAPTER_SCRIPT="${GOLIATH_ADAPTER_SCRIPT:-$HERE/$(basename "${BASH_SOURCE[0]}")}"
if [[ -z "${GOLIATH_BUNDLED_START:-}" ]]; then
  export GOLIATH_BUNDLED_START="$ROOT/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
fi

# Unset means "use the RunPod keepalive". A caller that sets the variable,
# including to another executable, keeps that command. GOLIATH_START_NO_EXEC
# is honored by the shared start and skips the keepalive entirely.
if [[ -z "${GOLIATH_START_KEEPALIVE:-}" ]]; then
  if [[ -x "$HERE/keepalive.sh" ]]; then
    export GOLIATH_START_KEEPALIVE="$HERE/keepalive.sh"
  elif [[ -x /start.sh ]]; then
    export GOLIATH_START_KEEPALIVE=/start.sh
  fi
fi

find_core() {
  local candidate
  for candidate in \
    "${GOLIATH_SHARED_WORKER_SCRIPT:-}" \
    "$ROOT/current/runtime-bundle/scripts/start_shared_worker.sh" \
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
  if [[ -n "${GOLIATH_START_KEEPALIVE:-}" && -x "${GOLIATH_START_KEEPALIVE}" && "${GOLIATH_START_NO_EXEC:-}" != "1" ]]; then
    printf '[goliath-worker-start] keeping the process alive\n' >&2
    exec "${GOLIATH_START_KEEPALIVE}"
  fi
  exit 1
}

exec bash "$core" "$@"
