#!/usr/bin/env bash
# Backward-compatible wrapper: create a Runpod worker pod with the global
# volume mounted at /work. All logic lives in deploy/workers/create_worker.sh
# (provider-neutral flow) and deploy/workers/providers/runpod.sh (Runpod API).
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../workers" && pwd)/create_worker.sh" --provider runpod "$@"
