#!/usr/bin/env bash
# Install the goliath-reclaim-leases oneshot and its timer on a host that can
# reach the workflow database (usually the gateway VM).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_reclaim_leases_timer.sh [options]

Options:
  --root PATH        Gateway install root (default: /opt/goliath-gateway)
  --env-file PATH    Database env (default: /etc/goliath/gateway.env)
  --user NAME        Default: goliath
  --dry-run
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

ROOT="/opt/goliath-gateway"
ENV_FILE="/etc/goliath/gateway.env"
SERVICE_USER="goliath"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --user) SERVICE_USER="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "Would install goliath-reclaim-leases.timer"
  exit 0
fi

rollouts_begin goliath-reclaim-leases "reclaim-leases $(hostname -s 2>/dev/null || hostname)"

for unit in goliath-reclaim-leases.service goliath-reclaim-leases.timer; do
  src="$REPO_ROOT/deploy/systemd/$unit"
  [[ -f "$src" ]] || die "Missing $src"
  tmp="$(mktemp)"
  sed \
    -e "s|__ROOT__|${ROOT}|g" \
    -e "s|__USER__|${SERVICE_USER}|g" \
    -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    "$src" >"$tmp"
  sudo install -m 644 "$tmp" "/etc/systemd/system/$unit"
  rm -f "$tmp"
done

sudo systemctl daemon-reload
sudo systemctl enable --now goliath-reclaim-leases.timer
sudo systemctl is-active --quiet goliath-reclaim-leases.timer
info "goliath-reclaim-leases.timer is active"
rollouts_set_message "goliath-reclaim-leases.timer active"
