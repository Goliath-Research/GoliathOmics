#!/usr/bin/env bash
# Install and restart the goliath-gateway systemd unit, then require it to be active.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_gateway_systemd.sh [options]

Options:
  --root PATH       Install root (default: /opt/goliath-gateway)
  --env-file PATH   EnvironmentFile (default: /etc/goliath/gateway.env)
  --user NAME       Service account (default: goliath)
  --enable-nginx    Install deploy/nginx/goliath-gateway.conf
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
ENABLE_NGINX=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --user) SERVICE_USER="${2:-}"; shift 2 ;;
    --enable-nginx) ENABLE_NGINX=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }
run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

UNIT_SRC="$REPO_ROOT/deploy/systemd/goliath-gateway.service"
[[ -f "$UNIT_SRC" ]] || die "Missing unit template: $UNIT_SRC"

if [[ "$DRY_RUN" -eq 0 ]]; then
  rollouts_begin goliath-gateway "gateway $(hostname -s 2>/dev/null || hostname)"
fi

render() {
  sed \
    -e "s|__ROOT__|${ROOT}|g" \
    -e "s|__USER__|${SERVICE_USER}|g" \
    -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    -e "s|__ARCH__|amd64|g" \
    "$1"
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "Would install goliath-gateway.service from $UNIT_SRC"
  exit 0
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  run sudo useradd --system --create-home --home-dir "$ROOT" --shell /usr/sbin/nologin "$SERVICE_USER" \
    || info "Could not create user $SERVICE_USER; using it anyway"
fi

tmp="$(mktemp)"
render "$UNIT_SRC" >"$tmp"
run sudo install -m 644 "$tmp" /etc/systemd/system/goliath-gateway.service
rm -f "$tmp"

if [[ "$ENABLE_NGINX" -eq 1 ]]; then
  ng="$REPO_ROOT/deploy/nginx/goliath-gateway.conf"
  [[ -f "$ng" ]] || die "Missing nginx config: $ng"
  run sudo install -m 644 "$ng" /etc/nginx/sites-available/goliath-gateway.conf
  run sudo ln -sfn /etc/nginx/sites-available/goliath-gateway.conf /etc/nginx/sites-enabled/goliath-gateway.conf
  run sudo nginx -t
  run sudo systemctl reload nginx
fi

run sudo systemctl daemon-reload
run sudo systemctl enable goliath-gateway.service
run sudo systemctl restart goliath-gateway.service
run sudo systemctl is-active --quiet goliath-gateway.service
info "goliath-gateway is active"
rollouts_set_message "goliath-gateway active on $(hostname -s 2>/dev/null || hostname)"
