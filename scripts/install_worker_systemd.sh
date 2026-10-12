#!/usr/bin/env bash
# Install and restart the goliath-worker systemd unit for one arch/capability.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_worker_systemd.sh [options]

Options:
  --root PATH
  --arch KEY                 aarch64 or amd64 (default: detect)
  --runtime PATH             Ignored except for logging (bundle that supplied this script)
  --capability NAME
  --detect-capabilities      Leave WORKER_CAPABILITY unset so the worker probes the node
  --env-file PATH            Default: /etc/goliath/worker.env
  --user NAME                Default: current user
  --dry-run
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
ARCH=""
RUNTIME=""
CAPABILITY="${WORKER_CAPABILITY:-}"
DETECT=0
ENV_FILE="/etc/goliath/worker.env"
SERVICE_USER="$(id -un)"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --runtime) RUNTIME="${2:-}"; shift 2 ;;
    --capability) CAPABILITY="${2:-}"; shift 2 ;;
    --detect-capabilities) DETECT=1; shift ;;
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --user) SERVICE_USER="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
if [[ "$DETECT" -eq 1 ]]; then
  CAPABILITY=""
fi
HOST="$(hostname -s 2>/dev/null || hostname)"
MSG="${ROLLOUTS_MESSAGE:-host=${HOST} arch=${ARCH} capability=${CAPABILITY:-unspecified}}"
info "worker unit runtime=${RUNTIME:-n/a} $MSG"

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "Would install goliath-worker.service and restart it"
  exit 0
fi

rollouts_begin goliath-worker "$MSG"

UNIT_SRC="$REPO_ROOT/deploy/systemd/goliath-worker.service"
[[ -f "$UNIT_SRC" ]] || UNIT_SRC="$SCRIPT_DIR/../deploy/systemd/goliath-worker.service"
[[ -f "$UNIT_SRC" ]] || die "Missing unit template: goliath-worker.service"

tmp="$(mktemp)"
sed \
  -e "s|__ROOT__|${ROOT}|g" \
  -e "s|__USER__|${SERVICE_USER}|g" \
  -e "s|__ENV_FILE__|${ENV_FILE}|g" \
  -e "s|__ARCH__|${ARCH}|g" \
  "$UNIT_SRC" >"$tmp"
if [[ -n "$CAPABILITY" ]]; then
  printf '\n[Service]\nEnvironment=WORKER_CAPABILITY=%s\n' "$CAPABILITY" >>"$tmp"
fi
sudo install -m 644 "$tmp" /etc/systemd/system/goliath-worker.service
rm -f "$tmp"
sudo systemctl daemon-reload
sudo systemctl enable goliath-worker.service
sudo systemctl restart goliath-worker.service
sudo systemctl is-active --quiet goliath-worker.service
info "goliath-worker is active ($MSG)"
rollouts_set_message "$MSG"
