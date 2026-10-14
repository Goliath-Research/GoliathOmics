#!/usr/bin/env bash
# Local contract for setup_gateway_nginx.sh. No root, no apt, no real nginx.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/setup_gateway_nginx.sh"
PROVISION="$ROOT/scripts/provision_gateway_node.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -f "$SCRIPT" ]] || fail "missing setup_gateway_nginx.sh"
[[ -x "$SCRIPT" ]] || fail "setup_gateway_nginx.sh is not executable"
[[ -x "$PROVISION" ]] || fail "provision_gateway_node.sh is not executable"
command -v openssl >/dev/null 2>&1 || fail "openssl is required"
bash "$SCRIPT" --help >/dev/null
bash "$PROVISION" --help >/dev/null

if grep -E '^[^#]*\<ufw\>[[:space:]]+enable([[:space:]]|$)' "$SCRIPT"; then
  fail "setup_gateway_nginx.sh must not enable ufw"
fi

make_fakes() {
  local bin="$1"
  local log="$2"
  local ufw_state="$3"
  mkdir -p "$bin"
  cat >"$bin/nginx" <<EOF
#!/bin/bash
printf '%s\n' "nginx \$*" >>"$log"
exit 0
EOF
  cat >"$bin/systemctl" <<EOF
#!/bin/bash
printf '%s\n' "systemctl \$*" >>"$log"
exit 0
EOF
  cat >"$bin/curl" <<EOF
#!/bin/bash
printf '%s\n' "curl \$*" >>"$log"
printf '%s' "\${CURL_CODE:-200}"
exit 0
EOF
  if [[ "$ufw_state" != "missing" ]]; then
    cat >"$bin/ufw" <<EOF
#!/bin/bash
printf '%s\n' "ufw \$*" >>"$log"
if [[ "\$1" == "status" ]]; then
  printf 'Status: ${ufw_state}\n'
fi
exit 0
EOF
    chmod +x "$bin/ufw"
  fi
  chmod +x "$bin/nginx" "$bin/systemctl" "$bin/curl"
}

cert_hash() {
  local dest="$1"
  sha256sum "$dest/etc/ssl/methyl-gateway/fullchain.pem" "$dest/etc/ssl/methyl-gateway/privkey.pem"
}

run_setup() {
  local dest="$1"
  shift
  env PATH="$FAKE_BIN:$PATH" CURL_CODE="${CURL_CODE:-200}" \
    bash "$SCRIPT" --dest-root "$dest" --probe-attempts 1 "$@"
}

FAKE_BIN="$(mktemp -d)"
LOG="$(mktemp)"
make_fakes "$FAKE_BIN" "$LOG" inactive
DEST="$(mktemp -d)"
run_setup "$DEST" --public-url "https://45.76.21.8/v1" >"$DEST/first.out"
cert_hash "$DEST" >"$DEST/hash1"
openssl verify -CAfile "$DEST/etc/ssl/methyl-gateway/fullchain.pem" \
  "$DEST/etc/ssl/methyl-gateway/fullchain.pem" >/dev/null
openssl x509 -in "$DEST/etc/ssl/methyl-gateway/fullchain.pem" -noout -ext subjectAltName \
  | grep -q '45.76.21.8' || fail "certificate SAN should include the public IP"
[[ "$(stat -c %a "$DEST/etc/ssl/methyl-gateway/privkey.pem")" == "600" ]] \
  || fail "private key must be mode 600"
[[ "$(stat -c %a "$DEST/etc/ssl/methyl-gateway/ca.pem")" == "644" ]] \
  || fail "ca.pem must be mode 644"
cmp -s "$DEST/etc/ssl/methyl-gateway/fullchain.pem" "$DEST/etc/ssl/methyl-gateway/ca.pem" \
  || fail "ca.pem should be a copy of the self-signed certificate"
site="$DEST/etc/nginx/sites-available/methyl-gateway.conf"
grep -q 'server_name 45.76.21.8;' "$site" || fail "site server_name should be the public IP"
grep -q 'server 127.0.0.1:8080;' "$site" || fail "site should proxy to 127.0.0.1:8080"
grep -q 'proxy_pass http://methyl_gateway;' "$site" || fail "site should proxy /v1/"
grep -q 'listen 443 default_server ssl http2;' "$site" || fail "site should listen on 443"
grep -q 'systemctl enable nginx' "$LOG" || fail "nginx should be enabled"
grep -q 'systemctl reload nginx' "$LOG" || fail "nginx should be reloaded"
grep -q 'https://127.0.0.1/v1/' "$LOG" || fail "probe should curl the loopback v1 URL"
grep -q -- '--cacert' "$LOG" || fail "probe should verify the issued certificate"
if grep -q 'ufw allow' "$LOG"; then
  fail "inactive ufw must not gain allow rules"
fi
if grep -q 'ufw enable' "$LOG"; then
  fail "ufw must not be enabled"
fi
grep -q 'issued self-signed certificate' "$DEST/first.out" || fail "first run should issue a certificate"

: >"$LOG"
run_setup "$DEST" --public-host "45.76.21.8" --san-ip "10.1.1.1" >"$DEST/second.out"
cert_hash "$DEST" >"$DEST/hash2"
cmp -s "$DEST/hash1" "$DEST/hash2" || fail "a later deploy must not regenerate the certificate"
grep -q 'reusing self-signed certificate' "$DEST/second.out" || fail "second run should reuse the certificate"
grep -q 'does not include 10.1.1.1' "$DEST/second.out" || fail "SAN drift should be reported without rotating the key"

ACTIVE_BIN="$(mktemp -d)"
ACTIVE_LOG="$(mktemp)"
ACTIVE_DEST="$(mktemp -d)"
make_fakes "$ACTIVE_BIN" "$ACTIVE_LOG" active
env PATH="$ACTIVE_BIN:$PATH" CURL_CODE=200 \
  bash "$SCRIPT" --dest-root "$ACTIVE_DEST" --probe-attempts 1 --public-host "45.76.21.8" >/dev/null
grep -q 'ufw allow 80/tcp' "$ACTIVE_LOG" || fail "active ufw should allow 80/tcp"
grep -q 'ufw allow 443/tcp' "$ACTIVE_LOG" || fail "active ufw should allow 443/tcp"
if grep -q 'ufw enable' "$ACTIVE_LOG"; then
  fail "active ufw must not be re-enabled"
fi
if grep -E 'ufw (deny|delete|reset|disable)' "$ACTIVE_LOG"; then
  fail "ufw SSH and other rules must be left alone"
fi

MISS_BIN="$(mktemp -d)"
MISS_LOG="$(mktemp)"
MISS_DEST="$(mktemp -d)"
make_fakes "$MISS_BIN" "$MISS_LOG" missing
env PATH="$MISS_BIN:$PATH" CURL_CODE=200 \
  bash "$SCRIPT" --dest-root "$MISS_DEST" --probe-attempts 1 --public-host "45.76.21.8" >/dev/null
if grep -q 'ufw ' "$MISS_LOG"; then
  fail "missing ufw should not be invoked"
fi

FAIL_DEST="$(mktemp -d)"
set +e
env PATH="$FAKE_BIN:$PATH" CURL_CODE=000 \
  bash "$SCRIPT" --dest-root "$FAIL_DEST" --probe-attempts 1 --public-host "45.76.21.8" \
  >"$FAIL_DEST/out" 2>"$FAIL_DEST/err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "a curl HTTP 000 probe must fail the deploy"
grep -q 'did not respond' "$FAIL_DEST/err" || fail "probe failure should say the proxy did not respond"
grep -q 'https://127.0.0.1/v1/' "$FAIL_DEST/err" || fail "probe failure should name the loopback URL"

set +e
bash "$SCRIPT" --public-host "45.76.21.8" --tls letsencrypt >"$FAIL_DEST/le.out" 2>"$FAIL_DEST/le.err"
status=$?
set -e
[[ "$status" -ne 0 ]] || fail "letsencrypt without a domain must fail"
grep -q 'requires --domain' "$FAIL_DEST/le.err" || fail "letsencrypt error should require a domain"

LE_DEST="$(mktemp -d)"
LE_DOMAIN="gw.example.com"
mkdir -p "$LE_DEST/etc/letsencrypt/live/$LE_DOMAIN"
openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
  -keyout "$LE_DEST/etc/letsencrypt/live/$LE_DOMAIN/privkey.pem" \
  -out "$LE_DEST/etc/letsencrypt/live/$LE_DOMAIN/fullchain.pem" \
  -subj "/CN=${LE_DOMAIN}" >/dev/null 2>&1
: >"$LOG"
env PATH="$FAKE_BIN:$PATH" CURL_CODE=200 \
  bash "$SCRIPT" --dest-root "$LE_DEST" --probe-attempts 1 \
  --public-host "$LE_DOMAIN" --tls letsencrypt --domain "$LE_DOMAIN" --email "ops@example.com" \
  >"$LE_DEST/out"
[[ -L "$LE_DEST/etc/ssl/methyl-gateway/fullchain.pem" ]] || fail "letsencrypt should symlink the live certificate"
grep -q 'reusing Let' "$LE_DEST/out" || fail "existing letsencrypt files should be reused"
if grep -q 'certbot' "$LOG"; then
  fail "certbot should not run when the live certificate already exists"
fi

SEED="$(mktemp -d)"
openssl req -x509 -newkey rsa:2048 -sha256 -days 2 -nodes \
  -keyout "$SEED/privkey.pem" \
  -out "$SEED/fullchain.pem" \
  -subj "/CN=issued.example.com" >/dev/null 2>&1
CERTBOT_BIN="$(mktemp -d)"
CERTBOT_LOG="$(mktemp)"
cp "$FAKE_BIN/nginx" "$FAKE_BIN/systemctl" "$FAKE_BIN/curl" "$FAKE_BIN/ufw" "$CERTBOT_BIN/"
cat >"$CERTBOT_BIN/certbot" <<EOF
#!/bin/bash
printf '%s\n' "certbot \$*" >>"$CERTBOT_LOG"
domain=""
prev=""
for arg in "\$@"; do
  if [[ "\$prev" == "-d" ]]; then
    domain="\$arg"
  fi
  prev="\$arg"
done
mkdir -p "$LE_DEST/issued/etc/letsencrypt/live/\$domain"
cp "$SEED/fullchain.pem" "$LE_DEST/issued/etc/letsencrypt/live/\$domain/fullchain.pem"
cp "$SEED/privkey.pem" "$LE_DEST/issued/etc/letsencrypt/live/\$domain/privkey.pem"
exit 0
EOF
chmod +x "$CERTBOT_BIN/certbot"
mkdir -p "$LE_DEST/issued"
env PATH="$CERTBOT_BIN:$PATH" CURL_CODE=200 \
  bash "$SCRIPT" --dest-root "$LE_DEST/issued" --probe-attempts 1 \
  --public-host "issued.example.com" --tls letsencrypt \
  --domain "issued.example.com" --email "ops@example.com" >/dev/null
grep -q -- '--keep-until-expiring' "$CERTBOT_LOG" || fail "certbot should keep an existing certificate until it expires"
grep -q -- '-d issued.example.com' "$CERTBOT_LOG" || fail "certbot should be asked for the profile domain"
lines="$(wc -l <"$CERTBOT_LOG" | tr -d ' ')"
env PATH="$CERTBOT_BIN:$PATH" CURL_CODE=200 \
  bash "$SCRIPT" --dest-root "$LE_DEST/issued" --probe-attempts 1 \
  --public-host "issued.example.com" --tls letsencrypt \
  --domain "issued.example.com" --email "ops@example.com" >/dev/null
lines_after="$(wc -l <"$CERTBOT_LOG" | tr -d ' ')"
[[ "$lines_after" == "$lines" ]] || fail "a second deploy must not request another certificate"

rm -rf "$FAKE_BIN" "$DEST" "$ACTIVE_BIN" "$ACTIVE_DEST" "$MISS_BIN" "$MISS_DEST" "$FAIL_DEST" "$LE_DEST" "$SEED" "$CERTBOT_BIN"
rm -f "$LOG" "$ACTIVE_LOG" "$MISS_LOG" "$CERTBOT_LOG"
echo "test_setup_gateway_nginx ok"
