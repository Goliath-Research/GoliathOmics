#!/usr/bin/env bash
# Contract tests for scripts/report_deploy.sh. No network.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORTER="$ROOT/scripts/report_deploy.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_eq() {
  local got="$1"
  local want="$2"
  local label="$3"
  if [[ "$got" != "$want" ]]; then
    fail "$label: got '$got' want '$want'"
  fi
}

install_fake_curl() {
  local bin="$1"
  mkdir -p "$bin"
  cat >"$bin/curl" <<'EOF'
#!/usr/bin/env bash
outfile=""
writefmt=""
data=""
url=""
config=""
auth_present=no
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) outfile="$2"; shift 2 ;;
    -w) writefmt="$2"; shift 2 ;;
    --data|--data-binary) data="$2"; shift 2 ;;
    -K) config="$2"; shift 2 ;;
    --cacert) printf '%s\n' "$2" >>"${CURL_LOG_DIR:?}/cacert"; shift 2 ;;
    -H|-X|--connect-timeout|--max-time) shift 2 ;;
    -sS|-s|-S|-f|-fsS) shift ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [[ -n "$config" && -f "$config" ]] && grep -q 'Authorization:' "$config"; then
  auth_present=yes
fi
mkdir -p "${CURL_LOG_DIR:?}"
printf '%s|%s\n' "${CURL_CA_BUNDLE-unset}" "${SSL_CERT_FILE-unset}" >>"$CURL_LOG_DIR/ca_env"
printf '%s\n' "$url" >>"$CURL_LOG_DIR/urls"
# The worker boot failure: a private LD_LIBRARY_PATH entry shadows the
# system libcurl and the curl binary cannot even start.
if [[ -n "${LD_LIBRARY_PATH:-}" || "${FAKE_CURL_FAIL:-}" == "broken" ]]; then
  echo "curl: symbol lookup error: curl: undefined symbol: curl_global_trace, version CURL_OPENSSL_4" >&2
  exit 127
fi
if [[ "${FAKE_CURL_FAIL:-}" == "dns" ]]; then
  echo "curl: (6) Could not resolve host: ${url#https://}" >&2
  printf '000'
  exit 6
fi
printf '%s\n' "$data" >>"$CURL_LOG_DIR/bodies"
printf '%s\n' "$auth_present" >>"$CURL_LOG_DIR/auth"
body='{}'
code=200
case "$url" in
  *exchange_user_api_key*)
    body='{"accessToken":"test-access-token"}'
    ;;
  *CreateDeployment*)
    body='{"deployment":{"name":"deployments/test-1"}}'
    ;;
  *)
    body='{}'
    ;;
esac
if [[ -n "$outfile" ]]; then
  printf '%s' "$body" >"$outfile"
fi
if [[ "$writefmt" == "%{http_code}" ]]; then
  printf '%s' "$code"
fi
EOF
  chmod +x "$bin/curl"
}

# Missing key must not fail and must not require the network.
out="$(mktemp)"
err="$(mktemp)"
env -u CURSOR_API_KEY ROLLOUTS_STATE_DIR="$TMP/nokey" bash "$REPORTER" start --service goliath-gateway >"$out" 2>"$err"
status=$?
assert_eq "$status" "0" "missing key exit"
grep -q 'skipping' "$err" || fail "missing key should say it skips"
grep -q 'CURSOR_API_KEY' "$err" || fail "skip line should name the variable"
assert_eq "$(wc -l <"$err" | tr -d ' ')" "1" "missing key logs exactly one line"
grep -q '^\[INFO\] report_deploy:' "$err" || fail "missing key is INFO, not WARN"
if grep -q 'sk-' "$out" "$err"; then
  fail "stdout/stderr looked like a secret"
fi

# Missing jq no-ops. Keep a PATH that can still exec bash.
jqless="$(mktemp -d)"
install_fake_curl "$jqless"
out="$(mktemp)"
err="$(mktemp)"
status=0
env PATH="$jqless" CURSOR_API_KEY=cursor_test_key_should_not_leak \
  ROLLOUTS_STATE_DIR="$TMP/nojq" \
  /usr/bin/bash "$REPORTER" start --service goliath-gateway >"$out" 2>"$err" || status=$?
assert_eq "$status" "0" "missing jq exit"
grep -q 'jq' "$err" || fail "missing jq should warn"
if grep -q 'cursor_test_key_should_not_leak' "$out" "$err"; then
  fail "api key leaked without jq"
fi

BIN="$TMP/bin"
install_fake_curl "$BIN"
export CURL_LOG_DIR="$TMP/log"
mkdir -p "$CURL_LOG_DIR"
export PATH="$BIN:$PATH"
export CURSOR_API_KEY="cursor_test_key_should_not_leak"
export ROLLOUTS_STATE_DIR="$TMP/state"
export GOLIATH_ENV="goliath-test"
unset DEPLOY_ACTOR || true

run_report() {
  local log_out log_err
  log_out="$(mktemp)"
  log_err="$(mktemp)"
  set +e
  bash "$REPORTER" "$@" >"$log_out" 2>"$log_err"
  local rc=$?
  set -e
  printf '%s\n' "$rc"
  cat "$log_out"
  echo "---stderr---"
  cat "$log_err"
}

result="$(run_report start --service goliathomics-runtime --version abc123 --message 'promote test')"
status="$(printf '%s\n' "$result" | head -n 1)"
assert_eq "$status" "0" "start exit"
printf '%s\n' "$result" | grep -q 'Opened Rollouts deployment deployments/test-1' || fail "start did not open"
printf '%s\n' "$result" | grep -q 'cursor_test_key_should_not_leak' && fail "api key leaked on start"
printf '%s\n' "$result" | grep -q 'test-access-token' && fail "access token leaked on start"
grep -q 'exchange_user_api_key' "$CURL_LOG_DIR/urls" || fail "did not exchange"
grep -q 'CreateEnvironment' "$CURL_LOG_DIR/urls" || fail "did not bootstrap environment"
grep -q 'CreateService' "$CURL_LOG_DIR/urls" || fail "did not bootstrap service"
grep -q 'CreateDeployment' "$CURL_LOG_DIR/urls" || fail "did not create deployment"
grep -q 'https://github.com/Goliath-Research/GoliathOmics' "$CURL_LOG_DIR/bodies" || fail "source uri missing"
grep -q 'environments/goliath-test' "$CURL_LOG_DIR/bodies" || fail "environment path missing"
grep -q 'services/goliathomics-runtime' "$CURL_LOG_DIR/bodies" || fail "service path missing"
grep -q '"deployVersion":"abc123"' "$CURL_LOG_DIR/bodies" || fail "version missing"
grep -qx 'yes' "$CURL_LOG_DIR/auth" || fail "authorization header was not sent"
state="$ROLLOUTS_STATE_DIR/goliath-test/goliathomics-runtime.json"
[[ -f "$state" ]] || fail "state file missing"
name="$(jq -r '.name' "$state")"
assert_eq "$name" "deployments/test-1" "state name"
terminal="$(jq -r '.terminal' "$state")"
assert_eq "$terminal" "false" "state open"

# Second start reuses the open deployment and does not append another create.
creates_before="$(grep -c 'CreateDeployment' "$CURL_LOG_DIR/urls")"
run_report start --service goliathomics-runtime --version abc123 >/dev/null
creates_after="$(grep -c 'CreateDeployment' "$CURL_LOG_DIR/urls")"
assert_eq "$creates_before" "$creates_after" "start reused open deployment"

result="$(run_report succeeded --service goliathomics-runtime)"
printf '%s\n' "$result" | grep -q 'outcome=succeeded' || fail "succeeded did not close"
grep -q 'AppendDeploymentEvent' "$CURL_LOG_DIR/urls" || fail "did not append"
grep -q '"succeeded":{}' "$CURL_LOG_DIR/bodies" || fail "succeeded payload missing"
terminal="$(jq -r '.terminal' "$state")"
assert_eq "$terminal" "true" "state closed"

result="$(run_report succeeded --service goliathomics-runtime)"
printf '%s\n' "$result" | grep -q 'already closed' || fail "second close should no-op"

# A new version opens again, then failed and aborted use their payloads.
: >"$CURL_LOG_DIR/bodies"
run_report start --service goliath-worker --version def456 --message 'host=a arch=amd64 capability=methyl-qc' >/dev/null
run_report failed --service goliath-worker --message 'host=a arch=amd64 capability=methyl-qc (exit 1)' >/dev/null
grep -q '"failed":{"message":"host=a arch=amd64 capability=methyl-qc (exit 1)"}' "$CURL_LOG_DIR/bodies" \
  || fail "failed payload missing"

# Reset terminal by starting a different service for aborted.
run_report start --service goliath-extractor --version ghi789 >/dev/null
run_report aborted --service goliath-extractor --message aborted >/dev/null
grep -q '"aborted":{}' "$CURL_LOG_DIR/bodies" || fail "aborted payload missing"

# Transport failure (HTTP 000): the curl exit code and message are logged,
# retries are bounded, the key never appears, and the pod CA overrides
# (gateway bundle in CURL_CA_BUNDLE/SSL_CERT_FILE) are not passed to curl.
: >"$CURL_LOG_DIR/urls"
: >"$CURL_LOG_DIR/ca_env"
rm -f "$CURL_LOG_DIR/cacert"
printf 'system-ca\n' >"$TMP/system-ca.pem"
printf 'gateway-only\n' >"$TMP/gateway-ca.pem"
result="$(FAKE_CURL_FAIL=dns REPORT_DEPLOY_ATTEMPTS=2 GOLIATH_SYSTEM_CA_BUNDLE="$TMP/system-ca.pem" \
  CURL_CA_BUNDLE="$TMP/gateway-ca.pem" SSL_CERT_FILE="$TMP/gateway-ca.pem" \
  run_report start --service goliath-worker --version zzz999)"
assert_eq "$(printf '%s\n' "$result" | head -n 1)" "0" "transport failure exit"
printf '%s\n' "$result" | grep -q 'curl exit 6: curl: (6) Could not resolve host' || fail "curl error not logged: $result"
printf '%s\n' "$result" | grep -q 'attempt 2/2' || fail "retry not logged"
printf '%s\n' "$result" | grep -q 'no HTTP response from https://api2.cursor.sh' || fail "000 not explained"
printf '%s\n' "$result" | grep -q "ignored SSL_CERT_FILE=$TMP/gateway-ca.pem" || fail "CA override not diagnosed"
printf '%s\n' "$result" | grep -q 'cursor_test_key_should_not_leak' && fail "api key leaked on transport failure"
assert_eq "$(grep -c exchange_user_api_key "$CURL_LOG_DIR/urls")" "2" "bounded retries"
grep -q CreateDeployment "$CURL_LOG_DIR/urls" && fail "must stop after a failed exchange"
assert_eq "$(sort -u "$CURL_LOG_DIR/ca_env")" "unset|unset" "curl must not inherit the gateway CA overrides"
assert_eq "$(sort -u "$CURL_LOG_DIR/cacert")" "$TMP/system-ca.pem" "curl uses the system CA bundle"

# LD_LIBRARY_PATH naming MethylExtractor's bundled libs (libcurl 7.81) must
# not reach curl: the reporter clears the loader overrides first.
rm -rf "$ROLLOUTS_STATE_DIR"
: >"$CURL_LOG_DIR/urls"
result="$(LD_LIBRARY_PATH="$TMP/methyl-extractor/lib" LD_PRELOAD="" \
  run_report start --service goliath-worker --version ld1)"
printf '%s\n' "$result" | grep -q 'curl_global_trace' && fail "curl ran with the caller's LD_LIBRARY_PATH: $result"
printf '%s\n' "$result" | grep -q 'Opened Rollouts deployment deployments/test-1' || fail "LD_LIBRARY_PATH run did not open: $result"

# curl that cannot run (exit 127) falls back to python3 urllib, once, with
# the same endpoints, payloads and bearer auth. Local HTTP server, no network.
cat >"$TMP/server.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
log = open(sys.argv[1], "a")
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        auth = "yes" if (self.headers.get("Authorization") or "").startswith("Bearer ") else "no"
        log.write(f"{self.path}|{auth}|{body}\n"); log.flush()
        if self.path.endswith("exchange"):
            out = {"accessToken": "test-access-token"}
        elif self.path.endswith("CreateDeployment"):
            out = {"deployment": {"name": "deployments/py-1"}}
        else:
            out = {}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass
srv = HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[2], "w").write(str(srv.server_address[1]))
srv.serve_forever()
PY
python3 "$TMP/server.py" "$TMP/server.log" "$TMP/server.port" &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; rm -rf "$TMP"' EXIT
for _ in $(seq 50); do [[ -s "$TMP/server.port" ]] && break; sleep 0.1; done
port="$(cat "$TMP/server.port")"
rm -rf "$ROLLOUTS_STATE_DIR"
: >"$CURL_LOG_DIR/urls"
result="$(FAKE_CURL_FAIL=broken REPORT_DEPLOY_TOKEN_URL="http://127.0.0.1:${port}/auth/exchange" \
  REPORT_DEPLOY_FACTORY_URL="http://127.0.0.1:${port}/factory" \
  run_report start --service goliath-worker --version py1)"
assert_eq "$(printf '%s\n' "$result" | head -n 1)" "0" "python fallback exit"
printf '%s\n' "$result" | grep -q 'using python3' || fail "fallback not announced: $result"
printf '%s\n' "$result" | grep -q 'Opened Rollouts deployment deployments/py-1' || fail "python fallback did not open: $result"
printf '%s\n' "$result" | grep -q 'cursor_test_key_should_not_leak' && fail "api key leaked in python fallback"
assert_eq "$(wc -l <"$CURL_LOG_DIR/urls" | tr -d ' ')" "1" "curl tried once, then python for every call"
grep -q '^/auth/exchange|yes|' "$TMP/server.log" || fail "python exchange missing auth"
grep -q '^/factory/CreateDeployment|yes|.*"deployVersion":"py1"' "$TMP/server.log" || fail "python CreateDeployment payload"
kill "$server_pid" 2>/dev/null || true

echo "test_report_deploy ok"
