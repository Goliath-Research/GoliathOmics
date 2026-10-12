#!/bin/bash
# Install methyl-gateway systemd unit on this VM.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_gateway_systemd.sh [options]

Install methyl-gateway on this VM's local disk. Do not point --root at /work
(the worker share). Default: /opt/methyl-gateway

Options:
  --root PATH           Gateway local root (default: /opt/methyl-gateway)
  --arch KEY            aarch64 or amd64 (default: detect)
  --runtime PATH        Path to runtime-bundle (default: <root>/runtime-bundle or this repo)
  --no-start            Install/enable only; do not start
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

ROOT="${GOLIATH_ROOT:-/opt/methyl-gateway}"
ARCH=""
RUNTIME=""
NO_START=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --runtime) RUNTIME="${2:-}"; shift 2 ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$ROOT" == /work || "$ROOT" == /work/* ]]; then
  echo "Gateway --root must be this VM's local disk, not the worker /work share (got $ROOT)." >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash $0 ..." >&2
  exit 1
fi

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
if [[ -x "$ROOT/venv-${ARCH}/bin/python" ]]; then
  VENV="$ROOT/venv-${ARCH}"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  VENV="$ROOT/venv"
else
  VENV="$ROOT/venv-${ARCH}"
fi
if [[ -d "$ROOT/runtime-bundle" ]]; then
  RUNTIME="${RUNTIME:-$(readlink -f "$ROOT/runtime-bundle")}"
elif [[ -d "$ROOT/current/runtime-bundle" ]]; then
  RUNTIME="${RUNTIME:-$(readlink -f "$ROOT/current/runtime-bundle")}"
else
  RUNTIME="${RUNTIME:-$SCRIPT_DIR/..}"
fi
DEPLOY="$RUNTIME/deploy/systemd"
[[ -d "$DEPLOY" ]] || DEPLOY="$SCRIPT_DIR/../deploy/systemd"

SRC="$DEPLOY/methyl-gateway.service"
[[ -f "$SRC" ]] || { echo "Missing $SRC" >&2; exit 1; }

rollouts_begin goliath-gateway "gateway $(hostname -s 2>/dev/null || hostname)"

sed -e "s|__GOLIATH_ROOT__|$ROOT|g" \
    -e "s|__GOLIATH_VENV__|$VENV|g" \
    "$SRC" >"/etc/systemd/system/methyl-gateway.service"

systemctl daemon-reload
systemctl enable methyl-gateway.service
[[ "$NO_START" -eq 0 ]] && systemctl restart methyl-gateway.service
systemctl status methyl-gateway.service --no-pager -l | head -15

# Lease reclaim timer (expired RUNNING → READY); same gateway.env credentials.
RECLAIM_INSTALL="$SCRIPT_DIR/install_reclaim_leases_timer.sh"
if [[ -f "$RECLAIM_INSTALL" ]]; then
  RECLAIM_ARGS=(--root "$ROOT" --arch "$ARCH" --runtime "$RUNTIME")
  [[ "$NO_START" -eq 1 ]] && RECLAIM_ARGS+=(--no-start)
  bash "$RECLAIM_INSTALL" "${RECLAIM_ARGS[@]}"
fi
rollouts_set_message "goliath-gateway active on $(hostname -s 2>/dev/null || hostname)"
