#!/usr/bin/env bash
# Report a deploy event to Cursor Rollouts.
#
#   report_deploy.sh bootstrap|start|succeeded|failed|aborted \
#     --service S [--env E] [--version SHA] [--message M]
#
# This helper never fails or blocks a deploy: missing key, curl, or jq, and
# any HTTP or parse error, are warnings. The process always exits 0.
# Secrets (API key, access token) are never printed.

set +x
set -uo pipefail

# Only OS tools run here (curl, jq, python3). A caller's LD_LIBRARY_PATH can
# name private library dirs that break them: with MethylExtractor's bundled
# Ubuntu 22.04 libcurl first on the path, /usr/bin/curl died with
# "symbol lookup error: curl: undefined symbol: curl_global_trace".
unset LD_LIBRARY_PATH LD_PRELOAD

trap 'exit 0' EXIT

# The URL overrides exist for tests (a local server); production uses the defaults.
TOKEN_URL="${REPORT_DEPLOY_TOKEN_URL:-https://api2.cursor.sh/auth/exchange_user_api_key}"
FACTORY_URL="${REPORT_DEPLOY_FACTORY_URL:-https://api.cursor.com/factory.v1.DeploymentsService}"
SOURCE_URI="${DEPLOY_SOURCE_URI:-https://github.com/Goliath-Research/GoliathOmics}"
# Every curl call is bounded: CONNECT_TIMEOUT per connect, MAX_TIME per
# attempt, ATTEMPTS tries on transport errors only (HTTP 4xx/5xx are not
# retried). Worst case per request: ATTEMPTS * MAX_TIME + backoff.
CONNECT_TIMEOUT="${REPORT_DEPLOY_CONNECT_TIMEOUT:-5}"
MAX_TIME="${REPORT_DEPLOY_MAX_TIME:-15}"
ATTEMPTS="${REPORT_DEPLOY_ATTEMPTS:-2}"
[[ "$CONNECT_TIMEOUT" =~ ^[0-9]+$ ]] || CONNECT_TIMEOUT=5
[[ "$MAX_TIME" =~ ^[0-9]+$ ]] || MAX_TIME=15
[[ "$ATTEMPTS" =~ ^[1-5]$ ]] || ATTEMPTS=2

warn() {
  echo "[WARN] report_deploy: $*" >&2
}

info() {
  echo "[INFO] report_deploy: $*" >&2
}

usage() {
  cat >&2 <<'EOF'
usage: report_deploy.sh bootstrap|start|succeeded|failed|aborted --service S [--env E] [--version SHA] [--message M]
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
usage: report_deploy.sh bootstrap|start|succeeded|failed|aborted --service S [--env E] [--version SHA] [--message M]
EOF
  exit 0
fi

COMMAND="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

SERVICE=""
ENVIRONMENT=""
VERSION=""
MESSAGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service) SERVICE="${2:-}"; shift 2 ;;
    --env) ENVIRONMENT="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --message) MESSAGE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      warn "unknown argument: $1"
      usage
      exit 0
      ;;
  esac
done

case "$COMMAND" in
  bootstrap|start|succeeded|failed|aborted) ;;
  *)
    warn "command must be bootstrap, start, succeeded, failed, or aborted"
    usage
    exit 0
    ;;
esac

if [[ -z "$SERVICE" ]]; then
  warn "--service is required"
  usage
  exit 0
fi

# curl is preferred; python3 (urllib) is the fallback when curl is missing
# or cannot run (exit 126/127).
HAVE_CURL=0
command -v curl >/dev/null 2>&1 && HAVE_CURL=1
if [[ "$HAVE_CURL" == 0 ]] && ! command -v python3 >/dev/null 2>&1; then
  warn "neither curl nor python3 is installed; skipping Rollouts report"
  exit 0
fi
if ! command -v jq >/dev/null 2>&1; then
  warn "jq is not installed; skipping Rollouts report"
  exit 0
fi

load_api_key() {
  if [[ -n "${CURSOR_API_KEY:-}" ]]; then
    return 0
  fi
  local file="/etc/goliath/rollouts.env"
  [[ -r "$file" ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    if [[ "$key" == "CURSOR_API_KEY" && -n "$val" ]]; then
      val="${val%\"}"
      val="${val#\"}"
      val="${val%\'}"
      val="${val#\'}"
      CURSOR_API_KEY="$val"
      export CURSOR_API_KEY
      return 0
    fi
  done <"$file"
}

load_api_key
if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  info "CURSOR_API_KEY is not set (env or /etc/goliath/rollouts.env); skipping Rollouts report"
  exit 0
fi

ENVIRONMENT="${ENVIRONMENT:-${GOLIATH_ENV:-production-work}}"

manifest_sha() {
  local manifest="$1"
  [[ -f "$manifest" ]] || return 1
  local sha
  sha="$(jq -r '(.sha // .commit // .git_sha // .source_sha // .revision // empty)' "$manifest" 2>/dev/null || true)"
  if [[ -n "$sha" && "$sha" != "null" ]]; then
    printf '%s' "$sha"
    return 0
  fi
  return 1
}

# Print the release directory ROOT/current points at, or nothing (exit 1)
# when it is missing, empty or invalid. Shared /work volumes cannot store
# symlinks (ln -s left a 0-byte regular file), so current is a pointer file
# holding the release path; a symlink or a real directory (older layouts) is
# still accepted.
goliath_current_dir() {
  local root="${1%/}" cur target=""
  cur="$root/current"
  if [[ -d "$cur" ]]; then
    target="$(cd "$cur" 2>/dev/null && pwd -P)" || return 1
  elif [[ -f "$cur" && -s "$cur" ]]; then
    target="$(head -n 1 "$cur" 2>/dev/null | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$target" || "$target" == /* ]] || target="$root/$target"
  fi
  [[ -n "$target" && -d "$target" ]] || return 1
  printf '%s\n' "$target"
}

if [[ -z "$VERSION" ]]; then
  sha=""
  for manifest in \
    "${GOLIATH_MANIFEST:-}" \
    "$(goliath_current_dir "${GOLIATH_ROOT:-/nonexistent}" 2>/dev/null)/manifest.json" \
    "$(goliath_current_dir "${ROOT:-/nonexistent}" 2>/dev/null)/manifest.json" \
    "$(goliath_current_dir /work/goliath 2>/dev/null)/manifest.json"
  do
    [[ -n "$manifest" ]] || continue
    if sha="$(manifest_sha "$manifest")"; then
      VERSION="$sha"
      break
    fi
  done
fi
if [[ -z "$VERSION" ]]; then
  if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    VERSION="$(git rev-parse HEAD 2>/dev/null || true)"
  fi
fi
if [[ -z "$VERSION" ]]; then
  VERSION="unknown"
fi

ACTOR="${DEPLOY_ACTOR:-}"
if [[ -z "$ACTOR" ]]; then
  user_name="$(id -un 2>/dev/null || echo user)"
  host_name="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo localhost)"
  ACTOR="${user_name}@${host_name}"
fi

state_file() {
  local dir="${ROLLOUTS_STATE_DIR:-}"
  if [[ -z "$dir" ]]; then
    if [[ -n "${RUNNER_TEMP:-}" && -d "${RUNNER_TEMP}" ]]; then
      dir="${RUNNER_TEMP}/goliath-rollouts"
    elif [[ -w /var/lib || -d /var/lib/goliath ]]; then
      dir="/var/lib/goliath/rollouts"
    else
      dir="${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/goliath/rollouts"
    fi
  fi
  if ! mkdir -p "${dir}/${ENVIRONMENT}" 2>/dev/null; then
    dir="/tmp/goliath-rollouts-${UID:-0}"
    mkdir -p "${dir}/${ENVIRONMENT}" 2>/dev/null || true
  fi
  printf '%s/%s/%s.json' "$dir" "$ENVIRONMENT" "$SERVICE"
}

STATE_FILE="$(state_file)"

# CA bundle for the public Cursor endpoints. Worker pods export
# SSL_CERT_FILE / REQUESTS_CA_BUNDLE (and sometimes CURL_CA_BUNDLE) pointing
# at a bundle built for the private gateway. curl honours SSL_CERT_FILE and
# CURL_CA_BUNDLE, so a gateway-only or unreadable file there makes every TLS
# handshake to api2.cursor.sh fail before any HTTP response (HTTP 000). These
# calls use the OS trust store explicitly and ignore those overrides.
# REPORT_DEPLOY_CA_BUNDLE overrides the choice.
cursor_ca_bundle() {
  local candidate
  for candidate in "${REPORT_DEPLOY_CA_BUNDLE:-}" "${GOLIATH_SYSTEM_CA_BUNDLE:-}" \
    /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt \
    /etc/ssl/ca-bundle.pem /etc/ssl/cert.pem; do
    if [[ -n "$candidate" && -r "$candidate" && -s "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

CA_BUNDLE="$(cursor_ca_bundle || true)"

# One line describing the TLS trust inputs, for failure diagnostics. Paths
# only; never contents or credentials.
tls_diag() {
  local d="cacert=${CA_BUNDLE:-curl-default}"
  [[ -n "${CURL_CA_BUNDLE:-}" ]] && d+=" ignored CURL_CA_BUNDLE=${CURL_CA_BUNDLE}"
  [[ -n "${SSL_CERT_FILE:-}" ]] && d+=" ignored SSL_CERT_FILE=${SSL_CERT_FILE}"
  [[ -n "${HTTPS_PROXY:-${https_proxy:-}}" ]] && d+=" https_proxy=set"
  printf '%s' "$d"
}

# First line of curl's stderr, with any secret scrubbed. curl's own error
# text names the host and the failing step, not headers.
curl_error_line() {
  local file="$1" line
  line="$(head -n 1 "$file" 2>/dev/null | tr -d '\r' | cut -c1-300)"
  if [[ -n "${CURSOR_API_KEY:-}" ]]; then line="${line//"$CURSOR_API_KEY"/<redacted>}"; fi
  if [[ -n "${ACCESS_TOKEN:-}" ]]; then line="${line//"$ACCESS_TOKEN"/<redacted>}"; fi
  printf '%s' "$line"
}

# POST JSON with python3's urllib: the fallback when curl is missing or
# cannot run. Same contract as http_post (HTTP_CODE, HTTP_BODY, CURL_RC,
# CURL_ERR). The bearer token travels in the environment, never argv.
# Extra args are curl-style -H 'Name: value' pairs.
http_post_python() {
  local url="$1" body="$2" out err rc=0
  shift 2
  local -a hdrs=()
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == -H && $# -ge 2 ]]; then hdrs+=("$2"); shift 2; else shift; fi
  done
  out="$(mktemp)"
  err="$(mktemp)"
  chmod 600 "$out" "$err" 2>/dev/null || true
  HTTP_CODE="$(
    REPORT_DEPLOY_BEARER="${CURL_AUTH_HEADER:-}" REPORT_DEPLOY_BODY="$body" \
    REPORT_DEPLOY_CAFILE="${CA_BUNDLE:-}" REPORT_DEPLOY_TIMEOUT="$MAX_TIME" \
      env -u SSL_CERT_FILE -u SSL_CERT_DIR -u REQUESTS_CA_BUNDLE \
      python3 - "$url" "$out" "${hdrs[@]}" 2>"$err" <<'PY'
import os, ssl, sys, urllib.error, urllib.request
url, out, *hdrs = sys.argv[1:]
req = urllib.request.Request(url, data=os.environ.get("REPORT_DEPLOY_BODY", "").encode(), method="POST")
req.add_header("Content-Type", "application/json")
for h in hdrs:
    k, _, v = h.partition(":")
    req.add_header(k.strip(), v.strip())
tok = os.environ.get("REPORT_DEPLOY_BEARER", "")
if tok:
    req.add_header("Authorization", "Bearer " + tok)
ctx = ssl.create_default_context(cafile=os.environ.get("REPORT_DEPLOY_CAFILE") or None)
try:
    with urllib.request.urlopen(req, timeout=float(os.environ.get("REPORT_DEPLOY_TIMEOUT") or 15), context=ctx) as r:
        code, data = r.status, r.read()
except urllib.error.HTTPError as e:
    code, data = e.code, e.read()
except Exception as e:  # transport error: no HTTP response
    print(f"python urllib: {type(e).__name__}: {e}", file=sys.stderr)
    print("000")
    sys.exit(7)
with open(out, "wb") as f:
    f.write(data)
print(f"{code:03d}")
PY
  )" || rc=$?
  [[ "$HTTP_CODE" =~ ^[0-9]{3}$ ]] || HTTP_CODE="000"
  CURL_RC="$rc"
  CURL_ERR=""
  if [[ "$rc" -ne 0 ]]; then
    HTTP_CODE="000"
    CURL_ERR="$(curl_error_line "$err")"
    warn "POST ${url} (python fallback): ${CURL_ERR:-no message} ($(tls_diag))"
  fi
  HTTP_BODY="$(cat "$out" 2>/dev/null || true)"
  rm -f "$out" "$err"
}

# POST JSON. Sets HTTP_CODE ("000" when no response arrived), HTTP_BODY,
# CURL_RC and CURL_ERR. Transport errors (curl exit != 0) are retried up to
# ATTEMPTS times; an HTTP status is returned as is. Falls back to python3
# when curl is missing or cannot execute (exit 126/127: a broken binary or
# shared library, which retrying cannot fix).
http_post() {
  if [[ "$HAVE_CURL" == 0 ]]; then
    http_post_python "$@"
    return
  fi

  local url="$1"
  local body="$2"
  shift 2
  local out err cfg attempt
  local -a tls=()
  out="$(mktemp)"
  err="$(mktemp)"
  cfg="$(mktemp)"
  chmod 600 "$out" "$err" "$cfg" 2>/dev/null || true
  if [[ -n "${CURL_AUTH_HEADER:-}" ]]; then
    printf 'header = "Authorization: Bearer %s"\n' "$CURL_AUTH_HEADER" >"$cfg"
  fi
  [[ -n "$CA_BUNDLE" ]] && tls=(--cacert "$CA_BUNDLE")
  CURL_RC=0
  CURL_ERR=""
  for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    : >"$err"
    CURL_RC=0
    HTTP_CODE="$(
      env -u CURL_CA_BUNDLE -u SSL_CERT_FILE -u SSL_CERT_DIR \
        curl -sS --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
        "${tls[@]}" \
        -o "$out" -w '%{http_code}' \
        -K "$cfg" \
        -X POST "$url" \
        -H 'Content-Type: application/json' \
        "$@" \
        --data-binary "$body" 2>"$err"
    )" || CURL_RC=$?
    [[ "$HTTP_CODE" =~ ^[0-9]{3}$ ]] || HTTP_CODE="000"
    if [[ "$CURL_RC" -eq 0 ]]; then
      break
    fi
    HTTP_CODE="000"
    CURL_ERR="$(curl_error_line "$err")"
    warn "POST ${url} attempt ${attempt}/${ATTEMPTS}: curl exit ${CURL_RC}: ${CURL_ERR:-no message} ($(tls_diag))"
    if [[ "$CURL_RC" -eq 126 || "$CURL_RC" -eq 127 ]] && command -v python3 >/dev/null 2>&1; then
      rm -f "$out" "$err" "$cfg"
      warn "curl cannot run; using python3 for Rollouts calls"
      HAVE_CURL=0
      http_post_python "$url" "$body" "$@"
      return
    fi
    if [[ "$attempt" -lt "$ATTEMPTS" ]]; then
      sleep "$attempt"
    fi
  done
  HTTP_BODY="$(cat "$out" 2>/dev/null || true)"
  rm -f "$out" "$err" "$cfg"
}

is_ok_or_exists() {
  local code="$1"
  local body="$2"
  if [[ "$code" =~ ^2[0-9][0-9]$ ]]; then
    return 0
  fi
  if [[ "$code" == "409" ]]; then
    return 0
  fi
  local c
  c="$(jq -r '(.code // empty) | tostring | ascii_downcase | gsub("-";"")' <<<"$body" 2>/dev/null || true)"
  if [[ "$c" == "alreadyexists" ]]; then
    return 0
  fi
  return 1
}

exchange_token() {
  CURL_AUTH_HEADER="$CURSOR_API_KEY"
  http_post "$TOKEN_URL" '{}'
  unset CURL_AUTH_HEADER
  if ! [[ "$HTTP_CODE" =~ ^2 ]]; then
    if [[ "$HTTP_CODE" == "000" ]]; then
      warn "token exchange failed: no HTTP response from ${TOKEN_URL} (curl exit ${CURL_RC:-?}: ${CURL_ERR:-unknown}); skipping Rollouts report"
    else
      warn "token exchange failed (HTTP ${HTTP_CODE}); check CURSOR_API_KEY; skipping Rollouts report"
    fi
    HTTP_BODY=""
    return 1
  fi
  ACCESS_TOKEN="$(jq -r '.accessToken // empty' <<<"$HTTP_BODY" 2>/dev/null || true)"
  HTTP_BODY=""
  if [[ -z "$ACCESS_TOKEN" ]]; then
    warn "token exchange response did not include an access token"
    return 1
  fi
  return 0
}

factory_post() {
  local method="$1"
  local body="$2"
  CURL_AUTH_HEADER="$ACCESS_TOKEN"
  http_post "${FACTORY_URL}/${method}" "$body" -H 'Connect-Protocol-Version: 1'
  unset CURL_AUTH_HEADER
}

write_state() {
  local name="$1"
  local terminal="$2"
  local parent
  parent="$(dirname "$STATE_FILE")"
  mkdir -p "$parent" 2>/dev/null || true
  umask 077
  jq -nc \
    --arg name "$name" \
    --arg version "$VERSION" \
    --arg service "$SERVICE" \
    --arg env "$ENVIRONMENT" \
    --argjson terminal "$terminal" \
    '{name:$name, version:$version, service:$service, env:$env, terminal:$terminal}' \
    >"$STATE_FILE" 2>/dev/null || warn "could not write Rollouts state file"
}

read_state_name() {
  [[ -f "$STATE_FILE" ]] || return 1
  jq -r '.name // empty' "$STATE_FILE" 2>/dev/null
}

state_is_terminal() {
  [[ -f "$STATE_FILE" ]] || return 1
  [[ "$(jq -r '.terminal // false' "$STATE_FILE" 2>/dev/null || echo false)" == "true" ]]
}

state_version() {
  [[ -f "$STATE_FILE" ]] || return 1
  jq -r '.version // empty' "$STATE_FILE" 2>/dev/null
}

if ! exchange_token; then
  unset ACCESS_TOKEN CURSOR_API_KEY || true
  exit 0
fi

bootstrap() {
  local env_body svc_body
  env_body="$(jq -nc --arg id "$ENVIRONMENT" '{environmentId:$id, environment:{displayName:$id}}')"
  factory_post "CreateEnvironment" "$env_body"
  if ! is_ok_or_exists "$HTTP_CODE" "$HTTP_BODY"; then
    warn "CreateEnvironment failed (HTTP ${HTTP_CODE})"
    HTTP_BODY=""
    return 0
  fi
  svc_body="$(jq -nc --arg id "$SERVICE" '{serviceId:$id, service:{displayName:$id}}')"
  factory_post "CreateService" "$svc_body"
  if ! is_ok_or_exists "$HTTP_CODE" "$HTTP_BODY"; then
    warn "CreateService failed (HTTP ${HTTP_CODE})"
    HTTP_BODY=""
    return 0
  fi
  HTTP_BODY=""
  echo "Rollouts catalog ready environment=${ENVIRONMENT} service=${SERVICE}"
}

start_deploy() {
  local existing existing_version
  if [[ -f "$STATE_FILE" ]] && ! state_is_terminal; then
    existing="$(read_state_name || true)"
    existing_version="$(state_version || true)"
    if [[ -n "$existing" && "$existing_version" == "$VERSION" ]]; then
      echo "Reusing open Rollouts deployment ${existing}"
      return 0
    fi
  fi
  local body name
  body="$(jq -nc \
    --arg source "$SOURCE_URI" \
    --arg env "environments/${ENVIRONMENT}" \
    --arg service "services/${SERVICE}" \
    --arg version "$VERSION" \
    --arg actor "$ACTOR" \
    '{deployment:{deploySourceUri:$source, environment:$env, service:$service, deployVersion:$version}, event:{started:{}, actor:$actor}}')"
  factory_post "CreateDeployment" "$body"
  if ! is_ok_or_exists "$HTTP_CODE" "$HTTP_BODY"; then
    warn "CreateDeployment failed (HTTP ${HTTP_CODE})"
    HTTP_BODY=""
    return 0
  fi
  name="$(jq -r '.deployment.name // .name // empty' <<<"$HTTP_BODY" 2>/dev/null || true)"
  HTTP_BODY=""
  if [[ -z "$name" ]]; then
    warn "CreateDeployment did not return deployment.name"
    return 0
  fi
  write_state "$name" false
  echo "Opened Rollouts deployment ${name}"
}

finish_deploy() {
  local kind="$1"
  local name
  name="$(read_state_name || true)"
  if [[ -z "$name" ]]; then
    warn "no open Rollouts deployment for ${ENVIRONMENT}/${SERVICE}; skipping ${kind}"
    return 0
  fi
  if state_is_terminal; then
    echo "Rollouts deployment ${name} already closed; skipping ${kind}"
    return 0
  fi
  local event body
  case "$kind" in
    succeeded)
      event="$(jq -nc --arg actor "$ACTOR" '{completed:{succeeded:{}}, actor:$actor}')"
      ;;
    failed)
      event="$(jq -nc --arg actor "$ACTOR" --arg message "${MESSAGE:-deploy failed}" \
        '{completed:{failed:{message:$message}}, actor:$actor}')"
      ;;
    aborted)
      event="$(jq -nc --arg actor "$ACTOR" '{aborted:{}, actor:$actor}')"
      ;;
    *)
      warn "unhandled finish kind"
      return 0
      ;;
  esac
  body="$(jq -nc --arg name "$name" --argjson event "$event" '{name:$name, event:$event}')"
  factory_post "AppendDeploymentEvent" "$body"
  if ! is_ok_or_exists "$HTTP_CODE" "$HTTP_BODY"; then
    warn "AppendDeploymentEvent failed (HTTP ${HTTP_CODE})"
    HTTP_BODY=""
    return 0
  fi
  HTTP_BODY=""
  write_state "$name" true
  echo "Closed Rollouts deployment ${name} outcome=${kind}"
}

case "$COMMAND" in
  bootstrap) bootstrap ;;
  start)
    bootstrap
    start_deploy
    ;;
  succeeded) finish_deploy succeeded ;;
  failed) finish_deploy failed ;;
  aborted) finish_deploy aborted ;;
esac

unset ACCESS_TOKEN CURSOR_API_KEY CURL_AUTH_HEADER HTTP_BODY || true
exit 0
