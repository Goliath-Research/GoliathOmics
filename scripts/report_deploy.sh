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

trap 'exit 0' EXIT

TOKEN_URL="https://api2.cursor.sh/auth/exchange_user_api_key"
FACTORY_URL="https://api.cursor.com/factory.v1.DeploymentsService"
SOURCE_URI="${DEPLOY_SOURCE_URI:-https://github.com/Goliath-Research/GoliathOmics}"
CONNECT_TIMEOUT=5
MAX_TIME=20

warn() {
  echo "[WARN] report_deploy: $*" >&2
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

if ! command -v curl >/dev/null 2>&1; then
  warn "curl is not installed; skipping Rollouts report"
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
  warn "CURSOR_API_KEY is unset (env or /etc/goliath/rollouts.env); skipping Rollouts report"
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

if [[ -z "$VERSION" ]]; then
  sha=""
  for manifest in \
    "${GOLIATH_MANIFEST:-}" \
    "${GOLIATH_ROOT:-}/current/manifest.json" \
    "${ROOT:-}/current/manifest.json" \
    "/work/goliath/current/manifest.json"
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

http_post() {
  local url="$1"
  local body="$2"
  shift 2
  local out err cfg
  out="$(mktemp)"
  err="$(mktemp)"
  cfg="$(mktemp)"
  chmod 600 "$out" "$err" "$cfg" 2>/dev/null || true
  if [[ -n "${CURL_AUTH_HEADER:-}" ]]; then
    printf 'header = "Authorization: Bearer %s"\n' "$CURL_AUTH_HEADER" >"$cfg"
  fi
  HTTP_CODE="$(
    curl -sS --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
      -o "$out" -w '%{http_code}' \
      -K "$cfg" \
      -X POST "$url" \
      -H 'Content-Type: application/json' \
      "$@" \
      --data-binary "$body" 2>"$err"
  )" || HTTP_CODE="000"
  HTTP_BODY="$(cat "$out" 2>/dev/null || true)"
  if [[ -s "$err" ]]; then
    warn "HTTP request to ${url} did not complete (curl exit, HTTP ${HTTP_CODE})"
  fi
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
    warn "token exchange failed (HTTP ${HTTP_CODE})"
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
