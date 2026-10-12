#!/bin/bash
# Install methyl-reclaim-leases oneshot + timer on the gateway host.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_reclaim_leases_timer.sh [options]

Installs systemd units that run methyl-reclaim-leases every 2 minutes
(using <root>/env/gateway.env — local gateway disk, not /work).

Options:
  --root PATH           Gateway local root (default: /opt/methyl-gateway)
  --arch KEY            aarch64 or amd64 (default: detect)
  --runtime PATH        Path to runtime-bundle (default: <root>/runtime-bundle)
  --no-start            Install/enable only; do not start timer
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

SVC_SRC="$DEPLOY/methyl-reclaim-leases.service"
TIMER_SRC="$DEPLOY/methyl-reclaim-leases.timer"
[[ -f "$SVC_SRC" ]] || { echo "Missing $SVC_SRC" >&2; exit 1; }
[[ -f "$TIMER_SRC" ]] || { echo "Missing $TIMER_SRC" >&2; exit 1; }

rollouts_begin goliath-reclaim-leases "reclaim-leases $(hostname -s 2>/dev/null || hostname)"

if [[ ! -x "$VENV/bin/methyl-reclaim-leases" ]]; then
  echo "WARN: $VENV/bin/methyl-reclaim-leases not found; install/upgrade methyl-gateway wheel first" >&2
fi

sed -e "s|__GOLIATH_ROOT__|$ROOT|g" \
    -e "s|__GOLIATH_VENV__|$VENV|g" \
    "$SVC_SRC" >"/etc/systemd/system/methyl-reclaim-leases.service"
cp -f "$TIMER_SRC" "/etc/systemd/system/methyl-reclaim-leases.timer"

systemctl daemon-reload
systemctl enable methyl-reclaim-leases.timer
[[ "$NO_START" -eq 0 ]] && systemctl restart methyl-reclaim-leases.timer
systemctl status methyl-reclaim-leases.timer --no-pager -l | head -20
rollouts_set_message "goliath-reclaim-leases.timer active"
echo "Manual run: systemctl start methyl-reclaim-leases.service"
