#!/usr/bin/env bash
# Install methyl-gateway on this VM and publish it through nginx.
# Local disk only. Do not point --root at the worker /work share.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/provision_gateway_node.sh [options]

Runs install_gateway_systemd.sh, then setup_gateway_nginx.sh.
Does not enable ufw. When ufw is already active, the nginx step opens
80/tcp and 443/tcp and leaves SSH rules alone.

Options:
  --root PATH           Gateway local root (default: /opt/methyl-gateway)
  --arch KEY            aarch64 or amd64 (default: detect)
  --release PATH        Extracted release whose wheel provides methyl-gateway
  --public-host HOST    Public IP or DNS name
  --public-url URL      https://host/v1 (used when --public-host is omitted)
  --tls MODE            self-signed (default) or letsencrypt
  --san-ip IP           SAN IP for a self-signed certificate
  --domain NAME         Required for letsencrypt
  --email ADDR          Required for letsencrypt
  --bind HOST           Upstream bind (default: 127.0.0.1)
  --port PORT           Upstream port (default: 8080)
  --no-start            Install units and nginx, but do not start or probe
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ROOT="${GOLIATH_ROOT:-/opt/methyl-gateway}"
ARCH=""
RELEASE=""
PUBLIC_HOST=""
PUBLIC_URL=""
TLS_MODE="self-signed"
SAN_IP=""
DOMAIN=""
EMAIL=""
BIND="127.0.0.1"
PORT="8080"
NO_START=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --release) RELEASE="${2:-}"; shift 2 ;;
    --public-host) PUBLIC_HOST="${2:-}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:-}"; shift 2 ;;
    --tls) TLS_MODE="${2:-}"; shift 2 ;;
    --san-ip) SAN_IP="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --bind) BIND="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$PUBLIC_HOST" && -z "$PUBLIC_URL" ]]; then
  echo "[ERROR] --public-host or --public-url is required" >&2
  exit 1
fi

systemd_args=(--root "$ROOT")
[[ -n "$ARCH" ]] && systemd_args+=(--arch "$ARCH")
[[ -n "$RELEASE" ]] && systemd_args+=(--release "$RELEASE")
[[ "$NO_START" -eq 1 ]] && systemd_args+=(--no-start)
bash "$SCRIPT_DIR/install_gateway_systemd.sh" "${systemd_args[@]}"

nginx_args=(--tls "$TLS_MODE" --bind "$BIND" --port "$PORT")
[[ -n "$PUBLIC_HOST" ]] && nginx_args+=(--public-host "$PUBLIC_HOST")
[[ -n "$PUBLIC_URL" ]] && nginx_args+=(--public-url "$PUBLIC_URL")
[[ -n "$SAN_IP" ]] && nginx_args+=(--san-ip "$SAN_IP")
[[ -n "$DOMAIN" ]] && nginx_args+=(--domain "$DOMAIN")
[[ -n "$EMAIL" ]] && nginx_args+=(--email "$EMAIL")
[[ "$NO_START" -eq 1 ]] && nginx_args+=(--skip-probe)
bash "$SCRIPT_DIR/setup_gateway_nginx.sh" "${nginx_args[@]}"
