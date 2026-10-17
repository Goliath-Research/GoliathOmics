#!/usr/bin/env bash
# Variables are shared between create_worker.sh and the sourced provider hooks.
# shellcheck disable=SC2034
# RunPod provider for deploy/workers/create_worker.sh (overrides the hooks in
# _interface.sh). Sourced, not executed.
#
# Uses the Runpod REST API v2 (https://api.runpod.io, POST /v2/pods), the only
# public REST API that takes a global volume mount with a custom path:
#   "mounts": {"global": [{"volumeId": "<id>", "path": "/work"}]}
# The v1 API (rest.runpod.io/v1) has no global volume support. Templates cannot
# carry a network or global volume mount, a console deploy mounts the global
# volume at /workspace, and Runpod refuses to add a mount to an existing pod, so
# workers must be created through this API.
#
# Needs curl and jq. RUNPOD_API_KEY is read from the environment, passed to
# curl through a private config file (never on the command line), and never
# printed. RUNPOD_API_BASE overrides https://api.runpod.io.

RUNPOD_HTTP_CODE=""

provider_defaults() {
  PROVIDER_DEFAULT_TEMPLATE="goliath-worker"
  PROVIDER_DEFAULT_STORAGE="work"
  PROVIDER_DEFAULT_GPU="NVIDIA L40S"
  PROVIDER_DEFAULT_CLOUD="SECURE"
  RUNPOD_API_BASE_URL="${RUNPOD_API_BASE:-https://api.runpod.io}"
  RUNPOD_API_BASE_URL="${RUNPOD_API_BASE_URL%/}"
}

provider_require_env() {
  command -v curl >/dev/null 2>&1 || die "curl is required"
  if [[ -z "${RUNPOD_API_KEY:-}" ]]; then
    die "RUNPOD_API_KEY is not set; export a Runpod API key with pods read/write access"
  fi
  case "$RUNPOD_API_KEY" in
    *[[:space:]\"\\]*) die "RUNPOD_API_KEY contains whitespace, quotes or backslashes; refusing to use it" ;;
  esac
  case "$OPT_CLOUD" in
    SECURE|COMMUNITY) ;;
    *) die "--cloud must be SECURE or COMMUNITY (got '$OPT_CLOUD')" ;;
  esac
  RUNPOD_CURL_CFG="$CW_TMP/curl.cfg"
  RUNPOD_RESP="$CW_TMP/resp.json"
  (
    umask 077
    printf 'header = "Authorization: Bearer %s"\n' "$RUNPOD_API_KEY" >"$RUNPOD_CURL_CFG"
  )
}

# _runpod_api METHOD PATH [BODY_FILE]: body in $RUNPOD_RESP, status in $RUNPOD_HTTP_CODE.
_runpod_api() {
  local method="$1" path="$2" body="${3:-}"
  local -a args=(-sS --connect-timeout 15 --max-time 90 -K "$RUNPOD_CURL_CFG"
    -H "Accept: application/json" -o "$RUNPOD_RESP" -w "%{http_code}")
  if [[ "$method" != GET ]]; then
    args+=(-X "$method")
  fi
  if [[ -n "$body" ]]; then
    args+=(-H "Content-Type: application/json" --data-binary "@$body")
  fi
  : >"$RUNPOD_RESP"
  RUNPOD_HTTP_CODE="$(curl "${args[@]}" "$RUNPOD_API_BASE_URL$path")" || {
    RUNPOD_HTTP_CODE=000
    return 1
  }
  [[ "$RUNPOD_HTTP_CODE" =~ ^2[0-9][0-9]$ ]]
}

_runpod_api_error() {
  local what="$1" detail=""
  detail="$(jq -r '[.title?, .detail?, (.errors? // empty | tostring)] | map(select(. != null and . != "")) | join(": ")' "$RUNPOD_RESP" 2>/dev/null || true)"
  [[ -n "$detail" ]] || detail="$(head -c 300 "$RUNPOD_RESP" 2>/dev/null || true)"
  die "$what failed (HTTP ${RUNPOD_HTTP_CODE:-000})${detail:+: $detail}"
}

# _runpod_api_list PATH KEY OUT: GET every page of a cursor-paginated list
# into OUT as one JSON array.
_runpod_api_list() {
  local path="$1" key="$2" out="$3" cursor="" sep page=0 url
  printf '[]' >"$out"
  while :; do
    page=$((page + 1))
    [[ "$page" -le 50 ]] || die "GET $path: too many pages"
    sep='?'
    [[ "$path" == *\?* ]] && sep='&'
    url="$path"
    if [[ -n "$cursor" ]]; then
      url="$path${sep}cursor=$(jq -rn --arg c "$cursor" '$c|@uri')"
    fi
    _runpod_api GET "$url" || _runpod_api_error "GET $path"
    jq -e --arg k "$key" 'has($k) and (.[$k] | type == "array")' "$RUNPOD_RESP" >/dev/null 2>&1 \
      || die "GET $path: response has no '$key' list"
    jq -s --arg k "$key" '.[0] + .[1][$k]' "$out" "$RUNPOD_RESP" >"$out.new" && mv "$out.new" "$out"
    if [[ "$(jq -r '.pagination.hasNextPage // false' "$RUNPOD_RESP")" != true ]]; then
      break
    fi
    cursor="$(jq -r '.pagination.nextCursor // empty' "$RUNPOD_RESP")"
    [[ -n "$cursor" ]] || break
  done
}

provider_resolve_template() {
  local tpl="$CW_TMP/runpod_template.json" name
  local -a ids
  _runpod_api_list /v2/templates templates "$CW_TMP/runpod_templates.json"
  mapfile -t ids < <(jq -r --arg t "$OPT_TEMPLATE" '.[] | select((.serverless // false) == false) | select(.id == $t or .name == $t) | .id' "$CW_TMP/runpod_templates.json")
  if [[ "${#ids[@]}" -eq 0 ]]; then
    die "pod template '$OPT_TEMPLATE' not found (by id or name); refusing to create"
  fi
  if [[ "${#ids[@]}" -gt 1 ]]; then
    die "pod template name '$OPT_TEMPLATE' matches ${#ids[@]} templates (${ids[*]}); pass --template <id>"
  fi
  TEMPLATE_ID="${ids[0]}"
  _runpod_api GET "/v2/templates/$TEMPLATE_ID" || _runpod_api_error "GET /v2/templates/$TEMPLATE_ID"
  cp "$RUNPOD_RESP" "$tpl"
  name="$(jq -r '.name // ""' "$tpl")"
  # A template persistent mount cannot be combined with a global mount (400),
  # and /work must be the shared global volume, not host-local disk.
  if jq -e '(.mounts.persistent // null) != null' "$tpl" >/dev/null; then
    die "template $TEMPLATE_ID has a persistent mount ($(jq -c '.mounts.persistent' "$tpl")); remove it, a global volume cannot be combined with it"
  fi
  log "template: $name ($TEMPLATE_ID), image $(jq -r '.image // "?"' "$tpl")"
}

provider_resolve_storage() {
  local vols="$CW_TMP/runpod_volumes.json" name
  local -a ids
  _runpod_api_list /v2/global-volumes globalVolumes "$vols"
  mapfile -t ids < <(jq -r --arg v "$OPT_STORAGE" '.[] | select(.id == $v or .name == $v) | .id' "$vols")
  if [[ "${#ids[@]}" -eq 0 ]]; then
    die "global volume '$OPT_STORAGE' not found (by id or name); refusing to create"
  fi
  if [[ "${#ids[@]}" -gt 1 ]]; then
    die "global volume name '$OPT_STORAGE' matches ${#ids[@]} volumes (${ids[*]}); pass --volume <id>"
  fi
  STORAGE_ID="${ids[0]}"
  name="$(jq -r --arg v "$STORAGE_ID" '.[] | select(.id == $v) | .name' "$vols")"
  log "global volume: $name ($STORAGE_ID) -> $MOUNT_PATH"
}

provider_list_worker_names() {
  _runpod_api_list /v2/pods pods "$CW_TMP/runpod_pods.json"
  jq -r '.[].name // empty' "$CW_TMP/runpod_pods.json"
}

provider_price() {
  local gpu_path cloud_key price total
  gpu_path="/v2/catalog/gpus/$(jq -rn --arg g "$OPT_GPU" '$g|@uri')"
  if _runpod_api GET "$gpu_path"; then
    cloud_key="$(tr '[:upper:]' '[:lower:]' <<<"$OPT_CLOUD")"
    if [[ "$(jq -r --arg c "$cloud_key" 'if has($c) then .[$c] else true end' "$RUNPOD_RESP")" == false ]]; then
      die "GPU '$OPT_GPU' is not offered on $OPT_CLOUD cloud"
    fi
    price="$(jq -r --arg c "$cloud_key" '.price[$c] // empty' "$RUNPOD_RESP")"
    if [[ -n "$price" ]]; then
      total="$(jq -rn --argjson p "$price" --argjson n "$OPT_GPU_COUNT" '($p * $n * 1000 | round) / 1000')"
      log "price: $OPT_GPU on $OPT_CLOUD cloud: \$$price/hr per GPU x $OPT_GPU_COUNT = \$$total/hr list price (pod cost after create is authoritative)"
    else
      log "price: not reported for $OPT_GPU on $OPT_CLOUD cloud"
    fi
  elif [[ "$RUNPOD_HTTP_CODE" == 404 ]]; then
    die "GPU type '$OPT_GPU' is not in the Runpod catalog (GET /v2/catalog/gpus lists valid ids)"
  else
    log "warning: could not read the GPU price (HTTP $RUNPOD_HTTP_CODE); continuing"
  fi
}

provider_build_request() {
  jq -n \
    --arg name "$WORKER_NAME" \
    --arg templateId "$TEMPLATE_ID" \
    --arg cloud "$OPT_CLOUD" \
    --arg gpu "$OPT_GPU" \
    --argjson count "$OPT_GPU_COUNT" \
    --arg volumeId "$STORAGE_ID" \
    --arg path "$MOUNT_PATH" \
    '{
       name: $name,
       templateId: $templateId,
       cloud: $cloud,
       gpu: {id: $gpu, count: $count},
       mounts: {global: [{volumeId: $volumeId, path: $path}]},
       startSsh: true
     }' >"$1"
}

provider_check_request() {
  jq -e --arg v "$STORAGE_ID" --arg p "$MOUNT_PATH" \
    '(.mounts.global | length) == 1 and .mounts.global[0].volumeId == $v and .mounts.global[0].path == $p' \
    "$1" >/dev/null
}

provider_request_target() { printf 'POST %s/v2/pods' "$RUNPOD_API_BASE_URL"; }

provider_create() {
  _runpod_api POST /v2/pods "$1" || _runpod_api_error "POST /v2/pods"
  WORKER_ID="$(jq -r '.id // empty' "$RUNPOD_RESP")"
  [[ -n "$WORKER_ID" ]] || die "POST /v2/pods returned HTTP $RUNPOD_HTTP_CODE without a pod id; check the console for a pod named $WORKER_NAME"
  log "created pod $WORKER_ID ($WORKER_NAME); cost $(jq -r '.cost // "?"' "$RUNPOD_RESP") USD/hr"
}

provider_describe() {
  local pod="$CW_TMP/runpod_pod.json"
  _runpod_api GET "/v2/pods/$1" || return 1
  cp "$RUNPOD_RESP" "$pod"
  W_NAME="$(jq -r '.name // empty' "$pod")"
  W_STATUS="$(jq -r '.status // empty' "$pod")"
  W_SSH="$(jq -r '.ssh.direct.command // .ssh.proxy.command // empty' "$pod")"
  RUNPOD_DIRECT_SSH="$(jq -r '.ssh.direct.command // empty' "$pod")"
  W_PUBLIC_IP="$(jq -r '([.runtime.ports[]? | select(.private == 22) | .ip // empty] + [.ssh.direct.host // empty]) | map(select(. != "")) | first // empty' "$pod")"
  W_MOUNTS="$(jq -r '.mounts.global[]? | "\(.volumeId):\(.path // "")"' "$pod")"
  W_MOUNTS_RAW="$(jq -c '.mounts // {}' "$pod")"
}

provider_wait_ready() {
  local id="$1" deadline=$((SECONDS + WAIT_SECS))
  while [[ -z "${RUNPOD_DIRECT_SSH:-}" || -z "$W_PUBLIC_IP" ]]; do
    [[ "$SECONDS" -lt "$deadline" ]] || break
    sleep "$POLL_SECS"
    provider_describe "$id" || true
  done
}

provider_terminate_hint() {
  printf 'Runpod console > Pods > %s > Terminate   (or DELETE %s/v2/pods/%s)\n' "$WORKER_NAME" "$RUNPOD_API_BASE_URL" "$1"
}
