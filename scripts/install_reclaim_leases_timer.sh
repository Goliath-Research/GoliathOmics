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
  --release PATH        Extracted goliathomics release (methyl-pipeline wheel)
  --no-start            Install/enable only; do not start timer
  -h, --help

Installs methyl-reclaim-leases into <root>/venv-<arch> from the release wheel
before enabling the timer. A missing binary would make the oneshot exit 203/EXEC.
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
RELEASE=""
NO_START=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --runtime) RUNTIME="${2:-}"; shift 2 ;;
    --release) RELEASE="${2:-}"; shift 2 ;;
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
VENV_INSTALL="$SCRIPT_DIR/install_gateway_venv.sh"
[[ -f "$VENV_INSTALL" ]] || { echo "Missing $VENV_INSTALL" >&2; exit 1; }
venv_args=(--root "$ROOT" --arch "$ARCH")
if [[ -n "$RELEASE" ]]; then
  venv_args+=(--release "$RELEASE")
fi
bash "$VENV_INSTALL" "${venv_args[@]}"
if [[ -x "$ROOT/venv-${ARCH}/bin/python" ]]; then
  VENV="$ROOT/venv-${ARCH}"
elif [[ -x "$ROOT/venv/bin/python" ]]; then
  VENV="$ROOT/venv"
else
  VENV="$ROOT/venv-${ARCH}"
fi
if [[ ! -x "$VENV/bin/methyl-reclaim-leases" ]]; then
  echo "[ERROR] $VENV/bin/methyl-reclaim-leases is missing. methyl-reclaim-leases.service would fail with status 203/EXEC." >&2
  exit 1
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

sed -e "s|__GOLIATH_ROOT__|$ROOT|g" \
    -e "s|__GOLIATH_VENV__|$VENV|g" \
    "$SVC_SRC" >"/etc/systemd/system/methyl-reclaim-leases.service"
cp -f "$TIMER_SRC" "/etc/systemd/system/methyl-reclaim-leases.timer"

systemctl daemon-reload
systemctl enable methyl-reclaim-leases.timer
[[ "$NO_START" -eq 0 ]] && systemctl restart methyl-reclaim-leases.timer
systemctl status methyl-reclaim-leases.timer --no-pager -l -n 20
rollouts_set_message "goliath-reclaim-leases.timer active"
echo "Manual run: systemctl start methyl-reclaim-leases.service"
