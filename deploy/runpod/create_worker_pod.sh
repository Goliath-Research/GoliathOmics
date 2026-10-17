#!/usr/bin/env bash
# Create a Runpod worker pod from the goliath-worker template with the shared
# global volume mounted at /work.
#
# Always deploy workers with this script, never from the Runpod console.
# Templates cannot carry a network or global volume mount (only a host-local
# `persistent` one), a console deploy mounts the global volume at /workspace,
# and Runpod refuses to add a mount to an existing pod. The worker bootstrap
# then stops with "persistent volume /work is not mounted and writable".
#
# Uses the Runpod REST API v2 (https://api.runpod.io, POST /v2/pods), the only
# public REST API that takes a global volume mount with a custom path:
#   "mounts": {"global": [{"volumeId": "<id>", "path": "/work"}]}
# The v1 API (rest.runpod.io/v1) has no global volume support.
#
# Needs curl and jq. RUNPOD_API_KEY is read from the environment, passed to
# curl through a private config file (never on the command line), and never
# printed. Creating a pod is billable, so it needs --yes; --dry-run prints the
# request body and makes only read-only GET calls.

set +x
set -euo pipefail

API_BASE="${RUNPOD_API_BASE:-https://api.runpod.io}"
API_BASE="${API_BASE%/}"
STANDARD_MOUNT_PATH=/work
NAME_PREFIX=goliath-worker

NAME=""
TEMPLATE="goliath-worker"
VOLUME="work"
MOUNT_PATH="$STANDARD_MOUNT_PATH"
GPU="NVIDIA L40S"
GPU_COUNT=1
CLOUD=SECURE
DRY_RUN=0
YES=0
ALLOW_NONSTANDARD_PATH=0
WAIT_SECS="${CREATE_WORKER_POD_WAIT_SECS:-180}"
POLL_SECS="${CREATE_WORKER_POD_POLL_SECS:-10}"

log() { printf '[create-worker-pod] %s\n' "$*" >&2; }
die() { printf '[create-worker-pod] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Create a goliath-worker pod with the global volume mounted at /work.

  --name NAME               Pod name (default: next free goliath-worker-N)
  --template NAME_OR_ID     Pod template (default: goliath-worker)
  --volume NAME_OR_ID       Global volume (default: work)
  --mount-path PATH         Mount path (default: /work; anything else also
                            needs --allow-nonstandard-path)
  --allow-nonstandard-path  Allow a --mount-path other than /work
  --gpu GPU_TYPE_ID         GPU type id (default: 'NVIDIA L40S')
  --gpu-count N             GPUs per pod (default: 1)
  --cloud SECURE|COMMUNITY  Cloud tier (default: SECURE)
  --wait SECS               Seconds to wait for SSH / public IP (default: 180)
  --dry-run                 Resolve everything, print the JSON body, no POST
  --yes                     Actually create the pod (billable)
  -h, --help                Show this help

Environment: RUNPOD_API_KEY (required), RUNPOD_API_BASE (default
https://api.runpod.io).
USAGE
}

need_arg() { [[ $# -ge 2 && -n "$2" ]] || die "$1 needs a value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) need_arg "$@"; NAME="$2"; shift 2 ;;
    --template) need_arg "$@"; TEMPLATE="$2"; shift 2 ;;
    --volume) need_arg "$@"; VOLUME="$2"; shift 2 ;;
    --mount-path) need_arg "$@"; MOUNT_PATH="$2"; shift 2 ;;
    --allow-nonstandard-path) ALLOW_NONSTANDARD_PATH=1; shift ;;
    --gpu) need_arg "$@"; GPU="$2"; shift 2 ;;
    --gpu-count) need_arg "$@"; GPU_COUNT="$2"; shift 2 ;;
    --cloud) need_arg "$@"; CLOUD="$2"; shift 2 ;;
    --wait) need_arg "$@"; WAIT_SECS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

# ---- static checks (no network) ----
command -v curl >/dev/null 2>&1 || die "curl is required"
command -v jq >/dev/null 2>&1 || die "jq is required"

if [[ -z "${RUNPOD_API_KEY:-}" ]]; then
  die "RUNPOD_API_KEY is not set; export a Runpod API key with pods read/write access"
fi
case "$RUNPOD_API_KEY" in
  *[[:space:]\"\\]*) die "RUNPOD_API_KEY contains whitespace, quotes or backslashes; refusing to use it" ;;
esac

[[ "$GPU_COUNT" =~ ^[1-9][0-9]*$ ]] || die "--gpu-count must be a positive integer (got '$GPU_COUNT')"
[[ "$WAIT_SECS" =~ ^[0-9]+$ ]] || die "--wait must be a non-negative integer"
[[ "$POLL_SECS" =~ ^[1-9][0-9]*$ ]] || die "CREATE_WORKER_POD_POLL_SECS must be a positive integer"
case "$CLOUD" in
  SECURE|COMMUNITY) ;;
  *) die "--cloud must be SECURE or COMMUNITY (got '$CLOUD')" ;;
esac
[[ "$MOUNT_PATH" == /* ]] || die "--mount-path must be an absolute path (got '$MOUNT_PATH')"
MOUNT_PATH="${MOUNT_PATH%/}"
[[ -n "$MOUNT_PATH" ]] || die "--mount-path cannot be /"
if [[ "$MOUNT_PATH" != "$STANDARD_MOUNT_PATH" && "$ALLOW_NONSTANDARD_PATH" != 1 ]]; then
  die "--mount-path $MOUNT_PATH is not $STANDARD_MOUNT_PATH; the worker bootstrap needs the volume at $STANDARD_MOUNT_PATH (pass --allow-nonstandard-path to override)"
fi
if [[ -n "$NAME" && ! "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  die "--name must be letters, digits, '.', '_' or '-' (got '$NAME')"
fi

TMP="$(umask 077 && mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CURL_CFG="$TMP/curl.cfg"
(
  umask 077
  printf 'header = "Authorization: Bearer %s"\n' "$RUNPOD_API_KEY" >"$CURL_CFG"
)

HTTP_CODE=""
RESP="$TMP/resp.json"

# api METHOD PATH [BODY_FILE]: response body in $RESP, status in $HTTP_CODE.
api() {
  local method="$1" path="$2" body="${3:-}"
  local -a args=(-sS --connect-timeout 15 --max-time 90 -K "$CURL_CFG"
    -H "Accept: application/json" -o "$RESP" -w "%{http_code}")
  if [[ "$method" != GET ]]; then
    args+=(-X "$method")
  fi
  if [[ -n "$body" ]]; then
    args+=(-H "Content-Type: application/json" --data-binary "@$body")
  fi
  : >"$RESP"
  HTTP_CODE="$(curl "${args[@]}" "$API_BASE$path")" || {
    HTTP_CODE=000
    return 1
  }
  [[ "$HTTP_CODE" =~ ^2[0-9][0-9]$ ]]
}

api_error() {
  local what="$1" detail=""
  detail="$(jq -r '[.title?, .detail?, (.errors? // empty | tostring)] | map(select(. != null and . != "")) | join(": ")' "$RESP" 2>/dev/null || true)"
  [[ -n "$detail" ]] || detail="$(head -c 300 "$RESP" 2>/dev/null || true)"
  die "$what failed (HTTP ${HTTP_CODE:-000})${detail:+: $detail}"
}

# api_list PATH KEY OUT: GET every page of a cursor-paginated list into OUT
# as one JSON array.
api_list() {
  local path="$1" key="$2" out="$3" cursor="" sep page=0
  printf '[]' >"$out"
  while :; do
    page=$((page + 1))
    [[ "$page" -le 50 ]] || die "GET $path: too many pages"
    sep='?'
    [[ "$path" == *\?* ]] && sep='&'
    local url="$path"
    if [[ -n "$cursor" ]]; then
      url="$path${sep}cursor=$(jq -rn --arg c "$cursor" '$c|@uri')"
    fi
    api GET "$url" || api_error "GET $path"
    jq -e --arg k "$key" 'has($k) and (.[$k] | type == "array")' "$RESP" >/dev/null 2>&1 \
      || die "GET $path: response has no '$key' list"
    jq -s --arg k "$key" '.[0] + .[1][$k]' "$out" "$RESP" >"$out.new" && mv "$out.new" "$out"
    if [[ "$(jq -r '.pagination.hasNextPage // false' "$RESP")" != true ]]; then
      break
    fi
    cursor="$(jq -r '.pagination.nextCursor // empty' "$RESP")"
    [[ -n "$cursor" ]] || break
  done
}

# ---- resolve template ----
api_list /v2/templates templates "$TMP/templates.json"
mapfile -t tpl_ids < <(jq -r --arg t "$TEMPLATE" '.[] | select((.serverless // false) == false) | select(.id == $t or .name == $t) | .id' "$TMP/templates.json")
if [[ "${#tpl_ids[@]}" -eq 0 ]]; then
  die "pod template '$TEMPLATE' not found (by id or name); refusing to create"
fi
if [[ "${#tpl_ids[@]}" -gt 1 ]]; then
  die "pod template name '$TEMPLATE' matches ${#tpl_ids[@]} templates (${tpl_ids[*]}); pass --template <id>"
fi
TEMPLATE_ID="${tpl_ids[0]}"
api GET "/v2/templates/$TEMPLATE_ID" || api_error "GET /v2/templates/$TEMPLATE_ID"
cp "$RESP" "$TMP/template.json"
TEMPLATE_NAME="$(jq -r '.name // ""' "$TMP/template.json")"
# A template persistent mount cannot be combined with a global mount (400),
# and /work must be the shared global volume, not host-local disk.
if jq -e '(.mounts.persistent // null) != null' "$TMP/template.json" >/dev/null; then
  die "template $TEMPLATE_ID has a persistent mount ($(jq -c '.mounts.persistent' "$TMP/template.json")); remove it, a global volume cannot be combined with it"
fi
log "template: $TEMPLATE_NAME ($TEMPLATE_ID), image $(jq -r '.image // "?"' "$TMP/template.json")"

# ---- resolve global volume ----
api_list /v2/global-volumes globalVolumes "$TMP/volumes.json"
mapfile -t vol_ids < <(jq -r --arg v "$VOLUME" '.[] | select(.id == $v or .name == $v) | .id' "$TMP/volumes.json")
if [[ "${#vol_ids[@]}" -eq 0 ]]; then
  die "global volume '$VOLUME' not found (by id or name); refusing to create"
fi
if [[ "${#vol_ids[@]}" -gt 1 ]]; then
  die "global volume name '$VOLUME' matches ${#vol_ids[@]} volumes (${vol_ids[*]}); pass --volume <id>"
fi
VOLUME_ID="${vol_ids[0]}"
VOLUME_NAME="$(jq -r --arg v "$VOLUME_ID" '.[] | select(.id == $v) | .name' "$TMP/volumes.json")"
log "global volume: $VOLUME_NAME ($VOLUME_ID) -> $MOUNT_PATH"

# ---- pick the pod name ----
api_list /v2/pods pods "$TMP/pods.json"
if [[ -z "$NAME" ]]; then
  n=1
  while jq -e --arg n "$NAME_PREFIX-$n" 'any(.[]; .name == $n)' "$TMP/pods.json" >/dev/null; do
    n=$((n + 1))
  done
  NAME="$NAME_PREFIX-$n"
elif jq -e --arg n "$NAME" 'any(.[]; .name == $n)' "$TMP/pods.json" >/dev/null; then
  die "a pod named '$NAME' already exists; pick another --name"
fi
log "pod name: $NAME"

# ---- GPU price (list price, per GPU per hour) ----
gpu_path="/v2/catalog/gpus/$(jq -rn --arg g "$GPU" '$g|@uri')"
if api GET "$gpu_path"; then
  cloud_key="$(tr '[:upper:]' '[:lower:]' <<<"$CLOUD")"
  if [[ "$(jq -r --arg c "$cloud_key" 'if has($c) then .[$c] else true end' "$RESP")" == false ]]; then
    die "GPU '$GPU' is not offered on $CLOUD cloud"
  fi
  price="$(jq -r --arg c "$cloud_key" '.price[$c] // empty' "$RESP")"
  if [[ -n "$price" ]]; then
    total="$(jq -rn --argjson p "$price" --argjson n "$GPU_COUNT" '($p * $n * 1000 | round) / 1000')"
    log "price: $GPU on $CLOUD cloud: \$$price/hr per GPU x $GPU_COUNT = \$$total/hr list price (pod cost after create is authoritative)"
  else
    log "price: not reported for $GPU on $CLOUD cloud"
  fi
elif [[ "$HTTP_CODE" == 404 ]]; then
  die "GPU type '$GPU' is not in the Runpod catalog (GET /v2/catalog/gpus lists valid ids)"
else
  log "warning: could not read the GPU price (HTTP $HTTP_CODE); continuing"
fi

# ---- request body ----
BODY="$TMP/body.json"
jq -n \
  --arg name "$NAME" \
  --arg templateId "$TEMPLATE_ID" \
  --arg cloud "$CLOUD" \
  --arg gpu "$GPU" \
  --argjson count "$GPU_COUNT" \
  --arg volumeId "$VOLUME_ID" \
  --arg path "$MOUNT_PATH" \
  '{
     name: $name,
     templateId: $templateId,
     cloud: $cloud,
     gpu: {id: $gpu, count: $count},
     mounts: {global: [{volumeId: $volumeId, path: $path}]},
     startSsh: true
   }' >"$BODY"

# Belt and braces: never POST a body without the global mount at the path.
jq -e --arg v "$VOLUME_ID" --arg p "$MOUNT_PATH" \
  '(.mounts.global | length) == 1 and .mounts.global[0].volumeId == $v and .mounts.global[0].path == $p' \
  "$BODY" >/dev/null || die "internal error: request body lacks the global mount"

if [[ "$DRY_RUN" == 1 ]]; then
  log "dry run: POST $API_BASE/v2/pods would send:"
  jq . "$BODY"
  exit 0
fi

if [[ "$YES" != 1 ]]; then
  jq . "$BODY" >&2
  die "creating a pod is billable; re-run with --yes to create it (or --dry-run to only print the body)"
fi

# ---- create ----
log "creating $NAME ..."
api POST /v2/pods "$BODY" || api_error "POST /v2/pods"
POD_ID="$(jq -r '.id // empty' "$RESP")"
[[ -n "$POD_ID" ]] || die "POST /v2/pods returned HTTP $HTTP_CODE without a pod id; check the console for a pod named $NAME"
log "created pod $POD_ID ($NAME); cost $(jq -r '.cost // "?"' "$RESP") USD/hr"

# ---- verify the mount and wait for SSH ----
mount_ok() {
  jq -e --arg v "$VOLUME_ID" --arg p "$MOUNT_PATH" \
    'any(.mounts.global[]?; .volumeId == $v and ((.path // "") | rtrimstr("/")) == $p)' "$1" >/dev/null
}

POD="$TMP/pod.json"
got=0
for _ in 1 2 3 4 5; do
  if api GET "/v2/pods/$POD_ID"; then
    cp "$RESP" "$POD"
    got=1
    break
  fi
  sleep 3
done
if [[ "$got" != 1 ]]; then
  die "could not GET pod $POD_ID (HTTP $HTTP_CODE) to verify its mounts; check it in the console and terminate it if /work is not the global volume"
fi
if ! mount_ok "$POD"; then
  log "ERROR: pod $POD_ID ($NAME) does not have global volume $VOLUME_ID mounted at $MOUNT_PATH"
  log "ERROR: mounts reported: $(jq -c '.mounts // {}' "$POD")"
  log "ERROR: this pod cannot run the worker and keeps billing. Terminate it (it was NOT terminated automatically):"
  log "ERROR:   Runpod console > Pods > $NAME > Terminate   (or DELETE $API_BASE/v2/pods/$POD_ID)"
  exit 3
fi
log "verified: global volume $VOLUME_ID mounted at $MOUNT_PATH"

ssh_cmd() { jq -r '.ssh.direct.command // .ssh.proxy.command // empty' "$POD"; }
public_ip() {
  jq -r '([.runtime.ports[]? | select(.private == 22) | .ip // empty] + [.ssh.direct.host // empty]) | map(select(. != "")) | first // empty' "$POD"
}

deadline=$((SECONDS + WAIT_SECS))
while [[ -z "$(jq -r '.ssh.direct.command // empty' "$POD")" || -z "$(public_ip)" ]]; do
  [[ "$SECONDS" -lt "$deadline" ]] || break
  sleep "$POLL_SECS"
  if api GET "/v2/pods/$POD_ID"; then
    cp "$RESP" "$POD"
  fi
done

SSH="$(ssh_cmd)"
IP="$(public_ip)"
printf 'pod_id=%s\n' "$POD_ID"
printf 'name=%s\n' "$(jq -r '.name // empty' "$POD")"
printf 'status=%s\n' "$(jq -r '.status // empty' "$POD")"
printf 'mount=%s:%s\n' "$VOLUME_ID" "$MOUNT_PATH"
printf 'ssh=%s\n' "${SSH:-<not ready yet; GET /v2/pods/$POD_ID later>}"
printf 'public_ip=%s\n' "${IP:-<not assigned yet>}"
