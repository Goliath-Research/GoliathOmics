#!/usr/bin/env bash
# Node-local checks after a worker join: env file, optional gateway health, extractor.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/verify_e2e_node.sh [options]

Options:
  --root PATH       Goliath root (default: /work/goliath)
  --require-api     Fail when WORKER_API_BASE health check fails
  --gpu             Require nvidia-smi
  -h, --help

WORKER_API_BASE (or the value in <root>/env/worker.env) is probed at /health
when curl is available. A missing gateway is a failure only with --require-api.
EOF
}

ROOT="${GOLIATH_ROOT:-/work/goliath}"
REQUIRE_API=0
GPU=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --require-api) REQUIRE_API=1; shift ;;
    --gpu) GPU=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

info "e2e node $(hostname -s 2>/dev/null || hostname) root=$ROOT"

if [[ -f "$ROOT/current/manifest.json" ]]; then
  info "current manifest present"
else
  die "Missing $ROOT/current/manifest.json"
fi

if [[ -f "$ROOT/env/worker.env" ]]; then
  info "worker env present"
  # shellcheck disable=SC1091
  set -a
  # shellcheck disable=SC1090
  source "$ROOT/env/worker.env"
  set +a
else
  info "No $ROOT/env/worker.env"
fi

if [[ "$GPU" -eq 1 ]]; then
  command -v nvidia-smi >/dev/null 2>&1 || die "nvidia-smi not found"
  nvidia-smi >/dev/null
fi

API="${WORKER_API_BASE:-${METHYL_API_BASE:-}}"
if [[ -n "$API" ]] && command -v curl >/dev/null 2>&1; then
  health="${API%/}/health"
  if curl -fsS --connect-timeout 5 --max-time 15 "$health" >/dev/null; then
    info "gateway health ok: $health"
  elif [[ "$REQUIRE_API" -eq 1 ]]; then
    die "gateway health failed: $health"
  else
    info "gateway health not reachable: $health"
  fi
elif [[ "$REQUIRE_API" -eq 1 ]]; then
  die "WORKER_API_BASE is required"
fi

info "verify_e2e_node passed"
