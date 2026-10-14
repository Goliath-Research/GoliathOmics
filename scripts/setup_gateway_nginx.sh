#!/usr/bin/env bash
# Install nginx, a TLS certificate, and the methyl-gateway site.
# Safe to re-run. Does not enable ufw and does not change SSH rules.
# When ufw is already active, opens 80/tcp and 443/tcp only.
#
# A Vultr cloud firewall, if the instance is in one, is separate from ufw.
# That group must allow inbound TCP 443 (and TCP 80 for the redirect or
# Let's Encrypt HTTP-01). This script cannot change the Vultr group.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup_gateway_nginx.sh [options]

Install nginx if it is missing, install deploy/nginx/methyl-gateway.conf as
the TLS proxy to the loopback gateway, and reload nginx. A self-signed
certificate is created once under /etc/ssl/methyl-gateway when that directory
has no key yet. Later runs keep the same key.

Options:
  --public-host HOST    Name clients use (IP or DNS). Required unless --public-url is set.
  --public-url URL      https://host/v1 form. The host is used as --public-host.
  --tls MODE            self-signed (default) or letsencrypt
  --san-ip IP           SAN IP for the self-signed certificate. Defaults to
                        --public-host when that host is an IPv4 address.
  --domain NAME         DNS name. Required when --tls letsencrypt.
  --email ADDR          ACME account email. Required when --tls letsencrypt.
  --bind HOST           Upstream host (default: 127.0.0.1)
  --port PORT           Upstream port (default: 8080)
  --site-src PATH       Site template (default: deploy/nginx/methyl-gateway.conf)
  --dest-root PATH      Prefix for /etc and /var. Used by tests. Omit on a host.
  --probe-attempts N    How many times to curl the local proxy (default: 12)
  --skip-probe          Do not curl https://127.0.0.1/v1/
  -h, --help

The post-install check curls https://127.0.0.1/v1/ through nginx (certificate
check disabled for the loopback name) and, for a self-signed certificate,
again with that certificate as the trust anchor. The deploy fails when nginx
does not return an HTTP response from the gateway.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PUBLIC_HOST=""
PUBLIC_URL=""
TLS_MODE="self-signed"
SAN_IP=""
DOMAIN=""
EMAIL=""
BIND="127.0.0.1"
PORT="8080"
SITE_SRC="$SCRIPT_DIR/../deploy/nginx/methyl-gateway.conf"
DEST_ROOT=""
PROBE_ATTEMPTS=12
SKIP_PROBE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --public-host) PUBLIC_HOST="${2:-}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:-}"; shift 2 ;;
    --tls) TLS_MODE="${2:-}"; shift 2 ;;
    --san-ip) SAN_IP="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --bind) BIND="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --site-src) SITE_SRC="${2:-}"; shift 2 ;;
    --dest-root) DEST_ROOT="${2:-}"; shift 2 ;;
    --probe-attempts) PROBE_ATTEMPTS="${2:-}"; shift 2 ;;
    --skip-probe) SKIP_PROBE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

host_from_url() {
  local host="$1"
  host="${host#*://}"
  host="${host%%/*}"
  host="${host%%\?*}"
  host="${host##*@}"
  if [[ "$host" == *:* ]]; then
    host="${host%%:*}"
  fi
  printf '%s' "$host"
}

is_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

assert_token() {
  local label="$1"
  local value="$2"
  [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]] || die "${label} is not a hostname or IPv4 address: ${value}"
}

if [[ -z "$PUBLIC_HOST" && -n "$PUBLIC_URL" ]]; then
  PUBLIC_HOST="$(host_from_url "$PUBLIC_URL")"
fi
[[ -n "$PUBLIC_HOST" ]] || die "--public-host or --public-url is required"
assert_token "public host" "$PUBLIC_HOST"
case "$TLS_MODE" in
  self-signed|letsencrypt) ;;
  *) die "--tls must be self-signed or letsencrypt" ;;
esac
[[ "$PORT" =~ ^[0-9]+$ ]] || die "--port must be an integer"
[[ "$PROBE_ATTEMPTS" =~ ^[0-9]+$ ]] || die "--probe-attempts must be an integer"
assert_token "upstream host" "$BIND"
if [[ -n "$SAN_IP" ]]; then
  is_ipv4 "$SAN_IP" || die "--san-ip must be an IPv4 address"
fi
if [[ "$TLS_MODE" == "letsencrypt" ]]; then
  [[ -n "$DOMAIN" ]] || die "letsencrypt requires --domain (a DNS name). An IP-only gateway uses --tls self-signed."
  [[ -n "$EMAIL" ]] || die "letsencrypt requires --email"
  assert_token "domain" "$DOMAIN"
  [[ "$EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "--email is not an email address"
fi
[[ -f "$SITE_SRC" ]] || die "nginx site template not found: $SITE_SRC"

if [[ -z "$DEST_ROOT" && "$(id -u)" -ne 0 ]]; then
  die "Run as root: sudo bash $0 ..."
fi
if [[ -n "$DEST_ROOT" ]]; then
  DEST_ROOT="${DEST_ROOT%/}"
fi

prefixed() {
  printf '%s%s' "$DEST_ROOT" "$1"
}

NGINX_ETC="$(prefixed /etc/nginx)"
SSL_DIR="$(prefixed /etc/ssl/methyl-gateway)"
WEBROOT="$(prefixed /var/www/html)"
LE_ROOT="$(prefixed /etc/letsencrypt)"

apt_install() {
  local pkg="$1"
  if [[ -n "$DEST_ROOT" ]]; then
    die "missing command for package ${pkg}; install it on the host (tests should stub it)"
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y "$pkg"
}

ensure_cmd() {
  local cmd="$1"
  local pkg="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    return 0
  fi
  info "installing ${pkg}"
  apt_install "$pkg"
  command -v "$cmd" >/dev/null 2>&1 || die "${cmd} is not available after installing ${pkg}"
}

ensure_cmd nginx nginx
ensure_cmd openssl openssl
if [[ "$SKIP_PROBE" -eq 0 ]]; then
  ensure_cmd curl curl
fi

ufw_is_active() {
  local status
  status="$(ufw status 2>/dev/null || true)"
  [[ "$status" == *"Status: active"* ]]
}

open_firewall() {
  if ! command -v ufw >/dev/null 2>&1; then
    info "ufw is not installed; leaving the host firewall unchanged"
    return 0
  fi
  if ufw_is_active; then
    # Allow the public proxy ports. Do not enable ufw and do not touch SSH.
    ufw allow 80/tcp
    ufw allow 443/tcp
    info "ufw is active; allowed 80/tcp and 443/tcp. SSH rules were not changed."
  else
    info "ufw is inactive; not enabling it and not adding rules"
  fi
}

issue_self_signed() {
  local cn san tmp old_umask rc
  cn="$PUBLIC_HOST"
  san=""
  if [[ -n "$SAN_IP" ]]; then
    san="IP:${SAN_IP}"
  elif is_ipv4 "$PUBLIC_HOST"; then
    san="IP:${PUBLIC_HOST}"
  else
    san="DNS:${PUBLIC_HOST}"
  fi
  if [[ -n "$DOMAIN" && "$san" != *"$DOMAIN"* ]]; then
    san="${san},DNS:${DOMAIN}"
  fi
  tmp="$(mktemp -d)"
  old_umask="$(umask)"
  umask 077
  cat >"$tmp/openssl.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no
[dn]
CN = ${cn}
[v3]
subjectAltName = ${san}
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyEncipherment,keyCertSign
extendedKeyUsage = serverAuth
EOF
  set +e
  openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout "$tmp/privkey.pem" \
    -out "$tmp/fullchain.pem" \
    -config "$tmp/openssl.cnf" >"$tmp/openssl.out" 2>&1
  rc=$?
  set -e
  umask "$old_umask"
  if [[ "$rc" -ne 0 ]]; then
    cat "$tmp/openssl.out" >&2
    rm -rf "$tmp"
    die "openssl failed to issue the self-signed certificate"
  fi
  install -m 644 "$tmp/fullchain.pem" "$SSL_DIR/fullchain.pem"
  install -m 600 "$tmp/privkey.pem" "$SSL_DIR/privkey.pem"
  rm -rf "$tmp"
  if [[ "${1:-}" == "bootstrap" ]]; then
    info "temporary certificate at ${SSL_DIR} so nginx can serve the ACME HTTP-01 challenge"
  else
    printf 'mode=self-signed host=%s san=%s\n' "$PUBLIC_HOST" "$san" >"$SSL_DIR/source"
    chmod 644 "$SSL_DIR/source"
    info "issued self-signed certificate at ${SSL_DIR} (${san})"
  fi
}

keep_self_signed() {
  [[ -s "$SSL_DIR/fullchain.pem" && -s "$SSL_DIR/privkey.pem" ]] || return 1
  [[ -L "$SSL_DIR/fullchain.pem" || -L "$SSL_DIR/privkey.pem" ]] && return 1
  openssl x509 -in "$SSL_DIR/fullchain.pem" -noout -checkend 0 >/dev/null 2>&1 || return 1
  return 0
}

warn_san_mismatch() {
  local text expected
  expected="${SAN_IP:-}"
  if [[ -z "$expected" ]] && is_ipv4 "$PUBLIC_HOST"; then
    expected="$PUBLIC_HOST"
  fi
  [[ -n "$expected" ]] || return 0
  text="$(openssl x509 -in "$SSL_DIR/fullchain.pem" -noout -ext subjectAltName 2>/dev/null || true)"
  if [[ "$text" != *"$expected"* ]]; then
    info "existing certificate does not include ${expected}. It was kept so a deploy does not rotate it. Delete ${SSL_DIR} and deploy again to issue a new certificate."
  fi
}

install_self_signed() {
  mkdir -p "$SSL_DIR"
  chmod 755 "$SSL_DIR"
  if keep_self_signed; then
    info "reusing self-signed certificate at ${SSL_DIR}"
    warn_san_mismatch
    if [[ ! -f "$SSL_DIR/source" ]]; then
      printf 'mode=self-signed host=%s\n' "$PUBLIC_HOST" >"$SSL_DIR/source"
      chmod 644 "$SSL_DIR/source"
    fi
  else
    issue_self_signed
  fi
  install -m 644 "$SSL_DIR/fullchain.pem" "$SSL_DIR/ca.pem"
}

letsencrypt_live_ready() {
  [[ -s "$LE_ROOT/live/$DOMAIN/fullchain.pem" && -s "$LE_ROOT/live/$DOMAIN/privkey.pem" ]]
}

link_letsencrypt_certs() {
  local live
  live="$LE_ROOT/live/$DOMAIN"
  ln -sfn "$live/fullchain.pem" "$SSL_DIR/fullchain.pem"
  ln -sfn "$live/privkey.pem" "$SSL_DIR/privkey.pem"
  ln -sfn "$live/fullchain.pem" "$SSL_DIR/ca.pem"
  printf 'mode=letsencrypt domain=%s\n' "$DOMAIN" >"$SSL_DIR/source"
  chmod 644 "$SSL_DIR/source"
}

install_renew_hook() {
  local dir hook
  dir="$LE_ROOT/renewal-hooks/deploy"
  hook="$dir/methyl-gateway"
  mkdir -p "$dir"
  # certbot renew updates the live files and does not reload nginx.
  cat >"$hook" <<'EOF'
#!/bin/sh
if systemctl is-active --quiet nginx; then
  systemctl reload nginx
else
  systemctl restart nginx
fi
EOF
  chmod 755 "$hook"
}

render_site() {
  local dest="$1"
  sed \
    -e "s/GATEWAY_HOSTNAME/${PUBLIC_HOST}/g" \
    -e "s/127\\.0\\.0\\.1:8080/${BIND}:${PORT}/g" \
    "$SITE_SRC" >"$dest"
}

install_site() {
  local avail link
  if [[ -d "$NGINX_ETC/sites-available" || -d "$NGINX_ETC/sites-enabled" || -n "$DEST_ROOT" ]]; then
    mkdir -p "$NGINX_ETC/sites-available" "$NGINX_ETC/sites-enabled"
    avail="$NGINX_ETC/sites-available/methyl-gateway.conf"
    render_site "$avail"
    link="$NGINX_ETC/sites-enabled/methyl-gateway.conf"
    ln -sfn "$avail" "$link"
    rm -f "$NGINX_ETC/sites-enabled/default"
  else
    mkdir -p "$NGINX_ETC/conf.d"
    avail="$NGINX_ETC/conf.d/methyl-gateway.conf"
    render_site "$avail"
  fi
  printf '%s' "$avail"
}

nginx_test() {
  local site="$1"
  local err changed
  err="$(mktemp)"
  if nginx -t >"$err" 2>&1; then
    rm -f "$err"
    return 0
  fi
  changed=0
  if grep -qiE 'ipv6|address family' "$err"; then
    info "nginx rejected IPv6 listeners; installing an IPv4-only site"
    sed -i '/\[::\]/d' "$site"
    changed=1
  fi
  if grep -qi 'http2' "$err"; then
    info "nginx rejected the http2 listen parameter; retrying without it"
    sed -i 's/ http2//g' "$site"
    changed=1
  fi
  if [[ "$changed" -eq 1 ]] && nginx -t >"$err" 2>&1; then
    rm -f "$err"
    return 0
  fi
  cat "$err" >&2
  rm -f "$err"
  die "nginx -t failed for ${site}"
}

reload_nginx() {
  systemctl enable nginx
  if systemctl is-active --quiet nginx; then
    systemctl reload nginx
  else
    systemctl restart nginx
  fi
}

proxy_ok() {
  local code="$1"
  [[ "$code" =~ ^[1-5][0-9][0-9]$ ]] || return 1
  [[ "$code" == "502" || "$code" == "504" ]] && return 1
  return 0
}

curl_code() {
  local err code
  err="$(mktemp)"
  code="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$@" 2>"$err" || true)"
  if [[ -s "$err" ]]; then
    sed 's/^/[probe] /' "$err" >&2
  fi
  rm -f "$err"
  printf '%s' "${code:-000}"
}

probe_proxy() {
  local attempt code
  [[ "$SKIP_PROBE" -eq 0 ]] || return 0
  code="000"
  for ((attempt = 1; attempt <= PROBE_ATTEMPTS; attempt++)); do
    code="$(curl_code -k "https://127.0.0.1/v1/")"
    if proxy_ok "$code"; then
      info "nginx responded to curl -k https://127.0.0.1/v1/ (HTTP ${code})"
      break
    fi
    if [[ "$attempt" -lt "$PROBE_ATTEMPTS" ]]; then
      sleep 1
    fi
  done
  if ! proxy_ok "$code"; then
    die "nginx did not respond at https://127.0.0.1/v1/ (last HTTP ${code:-000}). curl -k through the local nginx listener failed. methyl-gateway should be listening on ${BIND}:${PORT}. HTTP 000 means nothing accepted TLS on 127.0.0.1:443. HTTP 502/504 means nginx is up and the gateway process did not answer. This loopback check does not traverse the Vultr cloud firewall; that group must still allow inbound TCP 443 for workers."
  fi
  if [[ "$TLS_MODE" == "letsencrypt" ]]; then
    code="$(curl_code --resolve "${PUBLIC_HOST}:443:127.0.0.1" "https://${PUBLIC_HOST}/v1/")"
  else
    code="$(curl_code --cacert "$SSL_DIR/fullchain.pem" --resolve "${PUBLIC_HOST}:443:127.0.0.1" "https://${PUBLIC_HOST}/v1/")"
  fi
  if ! proxy_ok "$code"; then
    die "nginx answered on 127.0.0.1 but https://${PUBLIC_HOST}/v1/ failed certificate or proxy verification (HTTP ${code:-000}). The certificate at ${SSL_DIR}/fullchain.pem must match ${PUBLIC_HOST}. Delete ${SSL_DIR} and deploy again to issue a certificate for this host."
  fi
  info "verified https://${PUBLIC_HOST}/v1/ via nginx on 127.0.0.1 (HTTP ${code})"
}

publish_cert_hint() {
  local fingerprint
  fingerprint="$(openssl x509 -in "$SSL_DIR/fullchain.pem" -noout -fingerprint -sha256 2>/dev/null || true)"
  if [[ -n "$fingerprint" ]]; then
    info "gateway certificate ${fingerprint}"
  fi
  if [[ "$TLS_MODE" == "self-signed" ]]; then
    info "public certificate for workers: ${SSL_DIR}/ca.pem"
    info "Put that PEM in the RunPod secret GOLIATH_GATEWAY_CA_PEM (see deploy/runpod/README.md)."
  else
    info "Let's Encrypt certificate for ${DOMAIN} is in the public trust store. Workers do not need GOLIATH_GATEWAY_CA_PEM."
  fi
}

open_firewall
issue_le=0
if [[ "$TLS_MODE" == "letsencrypt" ]]; then
  mkdir -p "$SSL_DIR" "$WEBROOT" "$LE_ROOT"
  if letsencrypt_live_ready; then
    info "reusing Let's Encrypt certificate for ${DOMAIN}"
    link_letsencrypt_certs
  else
    # The site's port 443 block must load before nginx can serve the
    # HTTP-01 webroot. An existing key is kept; otherwise this is temporary.
    chmod 755 "$SSL_DIR"
    if ! keep_self_signed; then
      issue_self_signed bootstrap
    fi
    issue_le=1
  fi
else
  install_self_signed
fi
site="$(install_site)"
nginx_test "$site"
reload_nginx
if [[ "$issue_le" -eq 1 ]]; then
  ensure_cmd certbot certbot
  info "requesting a Let's Encrypt certificate for ${DOMAIN}"
  certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
    --email "$EMAIL" --agree-tos --non-interactive --keep-until-expiring
  letsencrypt_live_ready || die "Let's Encrypt did not produce ${LE_ROOT}/live/${DOMAIN}/fullchain.pem"
  link_letsencrypt_certs
  reload_nginx
fi
if [[ "$TLS_MODE" == "letsencrypt" ]]; then
  install_renew_hook
fi
probe_proxy
publish_cert_hint
info "methyl-gateway nginx site installed (${TLS_MODE}, ${PUBLIC_HOST} -> ${BIND}:${PORT})"
