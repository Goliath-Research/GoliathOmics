#!/usr/bin/env bash
# RunPod container start command for a self-provisioning goliath worker.
#
# The stock runpod-torch-v280 image starts /start.sh (Jupyter and SSH). Point
# the template's container start command at this script. It joins the gateway,
# starts methyl-worker, then execs /start.sh.
#
# Secrets are read only from the environment (RunPod secrets). This script
# never prints them. Do not run it under set -x.

set +x
set -euo pipefail

ROOT="${GOLIATH_ROOT:-/work/goliath}"
WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}"
ARCH="${GOLIATH_WORKER_ARCH:-amd64}"
LOG_DIR="${GOLIATH_LOG_DIR:-$ROOT/logs}"
export METHYL_WORKER_TOKEN_FILE="${METHYL_WORKER_TOKEN_FILE:-/etc/methyl/worker-token}"
export GOLIATH_ENV="${GOLIATH_ENV:-goliath-test}"

GPU_DESC=""
HOST_SHORT=""

log() { printf '[goliath-worker-start] %s\n' "$*"; }
die() { printf '[goliath-worker-start] ERROR: %s\n' "$*" >&2; exit 1; }

wait_for_work() {
  local i
  for ((i = 1; i <= 90; i++)); do
    if [[ -d "$WORK_MOUNT" && -w "$WORK_MOUNT" ]]; then
      return 0
    fi
    sleep 2
  done
  die "persistent volume ${WORK_MOUNT} is not mounted and writable"
}

pod_id() {
  local id="${RUNPOD_POD_ID:-}"
  if [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    printf '%s' "$id"
    return 0
  fi
  hostname -s 2>/dev/null || hostname 2>/dev/null || printf '%s' "unknown-pod"
}

pod_dir() {
  printf '%s/runpod/%s' "$ROOT" "$(pod_id)"
}

resolve_tool() {
  local override="$1"
  local name="$2"
  local candidate
  if [[ -n "$override" ]]; then
    [[ -f "$override" ]] || die "missing ${name} at ${override}"
    printf '%s' "$override"
    return 0
  fi
  for candidate in \
    "$ROOT/current/runtime-bundle/scripts/$name" \
    "$ROOT/current/scripts/$name"
  do
    if [[ -f "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

curl_download() {
  local url="$1"
  local dest="$2"
  local token="$3"
  local accept="${4:-}"
  local header="" old_umask rc=0
  local -a args=(-fsSL --retry 3 --retry-delay 2)
  if [[ -n "$token" ]]; then
    header="$ROOT/tmp/github.headers"
    mkdir -p "$ROOT/tmp"
    old_umask="$(umask)"
    umask 077
    printf 'Authorization: Bearer %s\n' "$token" >"$header"
    umask "$old_umask"
    chmod 600 "$header"
    args+=(-H "@${header}")
  fi
  if [[ -n "$accept" ]]; then
    args+=(-H "Accept: ${accept}")
  fi
  args+=(-o "$dest" "$url")
  curl "${args[@]}" || rc=$?
  if [[ -n "$header" ]]; then
    rm -f "$header"
  fi
  [[ "$rc" -eq 0 ]] || die "release download failed"
}

download_latest_release() {
  local repo="$1"
  local token="$2"
  local dest="$3"
  local api meta asset_spec asset_id asset_name asset_url
  if [[ -n "${GOLIATH_RELEASE_TAG:-}" ]]; then
    api="https://api.github.com/repos/${repo}/releases/tags/${GOLIATH_RELEASE_TAG}"
  else
    api="https://api.github.com/repos/${repo}/releases/latest"
  fi
  meta="$ROOT/tmp/release.json"
  curl_download "$api" "$meta" "$token" "application/vnd.github+json"
  asset_spec="$(python3 -c 'import json,sys
rel=json.load(open(sys.argv[1], encoding="utf-8"))
for asset in rel.get("assets") or []:
    name=asset.get("name") or ""
    if name.startswith("goliathomics-release-") and name.endswith(".tar.gz"):
        print(asset["id"])
        print(name)
        break
' "$meta")"
  rm -f "$meta"
  [[ -n "$asset_spec" ]] || die "GitHub release has no goliathomics-release-*.tar.gz asset"
  asset_id="${asset_spec%%$'\n'*}"
  asset_name="${asset_spec#*$'\n'}"
  [[ "$asset_id" =~ ^[0-9]+$ ]] || die "release asset id was not numeric"
  log "downloading ${asset_name}"
  asset_url="https://api.github.com/repos/${repo}/releases/assets/${asset_id}"
  curl_download "$asset_url" "$dest" "$token" "application/octet-stream"
}

ensure_current() {
  if [[ -L "$ROOT/current" && ! -e "$ROOT/current" ]]; then
    die "$ROOT/current is a broken symlink"
  fi
  if [[ -e "$ROOT/current" ]]; then
    log "using existing $ROOT/current"
    return 0
  fi
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  local url="${GOLIATH_RELEASE_URL:-}"
  local repo="${GOLIATH_GITHUB_REPO:-Goliath-Research/GoliathOmics}"
  if [[ -z "$url" && -z "$token" ]]; then
    die "$ROOT/current does not exist and neither GH_TOKEN/GITHUB_TOKEN nor GOLIATH_RELEASE_URL is set; cannot bootstrap a release bundle"
  fi
  command -v python3 >/dev/null 2>&1 || die "python3 is required to read the release manifest"
  command -v curl >/dev/null 2>&1 || die "curl is required to download the release bundle"
  mkdir -p "$ROOT/releases" "$ROOT/tmp"
  local tarball="$ROOT/tmp/goliathomics-release.tar.gz"
  if [[ -n "$url" ]]; then
    log "bootstrapping release from GOLIATH_RELEASE_URL"
    curl_download "$url" "$tarball" "$token"
  else
    log "bootstrapping the latest GitHub release for ${repo}"
    download_latest_release "$repo" "$token" "$tarball"
  fi
  local extract="$ROOT/tmp/extract"
  rm -rf "$extract"
  mkdir -p "$extract"
  tar -xzf "$tarball" -C "$extract"
  rm -f "$tarball"
  [[ -f "$extract/manifest.json" ]] || die "release bundle has no manifest.json"
  local ver
  ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version",""))' "$extract/manifest.json")"
  [[ "$ver" =~ ^[A-Za-z0-9._+-]+$ ]] || die "release manifest version is missing or not a safe path component"
  local dest="$ROOT/releases/$ver"
  rm -rf "$dest"
  mv "$extract" "$dest"
  local promote="$dest/runtime-bundle/scripts/promote_release.sh"
  [[ -f "$promote" ]] || die "release bundle has no runtime-bundle/scripts/promote_release.sh"
  local -a promote_args=(--root "$ROOT" --release "$dest" --arch "$ARCH" --skip-docker-pull)
  if [[ -n "${WORKER_API_BASE:-}" ]]; then
    promote_args+=(--worker-api-base "$WORKER_API_BASE")
  fi
  log "promoting ${dest}"
  bash "$promote" "${promote_args[@]}"
  [[ -e "$ROOT/current" ]] || die "promote did not create $ROOT/current"
}

maybe_reexec() {
  [[ "${GOLIATH_START_REEXECED:-}" == "1" ]] && return 0
  local bundled="$ROOT/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  [[ -f "$bundled" ]] || return 0
  local self target
  self="$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")"
  target="$(readlink -f "$bundled" 2>/dev/null || printf '%s' "$bundled")"
  [[ "$self" == "$target" ]] && return 0
  log "reexec bundled start script"
  export GOLIATH_START_REEXECED=1
  exec bash "$bundled" "$@"
}

detect_gpu() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    die "nvidia-smi is not installed; goliath-worker requires a GPU"
  fi
  local raw
  raw="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || true)"
  raw="${raw%%$'\n'*}"
  raw="$(printf '%s' "$raw" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  if [[ -z "$raw" ]]; then
    die "nvidia-smi did not report a GPU; goliath-worker requires a GPU (RTX 6000 Ada)"
  fi
  GPU_DESC="$raw"
  log "GPU detected via nvidia-smi: ${GPU_DESC}"
}

append_kv() {
  local dest="$1"
  local key="$2"
  local val="$3"
  [[ -n "$val" ]] || return 0
  val="${val//\\/\\\\}"
  val="${val//\"/\\\"}"
  val="${val//$'\n'/}"
  printf '%s="%s"\n' "$key" "$val" >>"$dest"
}

write_worker_env() {
  local dest="$ROOT/env/worker.env"
  local old_umask
  local b2_key="${B2_ACCESS_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}"
  local b2_secret="${B2_SECRET_ACCESS_KEY:-${AWS_SECRET_ACCESS_KEY:-}}"
  local b2_endpoint="${B2_ENDPOINT:-${AWS_ENDPOINT_URL:-}}"
  local b2_bucket="${B2_BUCKET:-${GOLIATH_SHARED_BUCKET:-}}"
  local long_key="${LONGTERM_ACCESS_KEY_ID:-${GOLIATH_LONGTERM_ACCESS_KEY_ID:-}}"
  local long_secret="${LONGTERM_SECRET_ACCESS_KEY:-${GOLIATH_LONGTERM_SECRET_ACCESS_KEY:-}}"
  local long_endpoint="${LONGTERM_ENDPOINT:-${GOLIATH_LONGTERM_ENDPOINT:-}}"
  mkdir -p "$ROOT/env"
  old_umask="$(umask)"
  umask 077
  : >"$dest"
  append_kv "$dest" WORKER_API_BASE "${WORKER_API_BASE:-}"
  append_kv "$dest" GOLIATH_ENV "$GOLIATH_ENV"
  append_kv "$dest" GOLIATH_SHARED_PROVIDER "b2"
  append_kv "$dest" GOLIATH_SHARED_BUCKET "$b2_bucket"
  append_kv "$dest" AWS_ENDPOINT_URL "$b2_endpoint"
  append_kv "$dest" AWS_ACCESS_KEY_ID "$b2_key"
  append_kv "$dest" AWS_SECRET_ACCESS_KEY "$b2_secret"
  append_kv "$dest" GOLIATH_LONGTERM_PROVIDER "myqnapcloud"
  append_kv "$dest" GOLIATH_LONGTERM_BUCKET "${LONGTERM_BUCKET:-${GOLIATH_LONGTERM_BUCKET:-}}"
  append_kv "$dest" GOLIATH_LONGTERM_ENDPOINT "$long_endpoint"
  append_kv "$dest" GOLIATH_LONGTERM_HOST "${LONGTERM_HOST:-${GOLIATH_LONGTERM_HOST:-}}"
  append_kv "$dest" GOLIATH_LONGTERM_SHARE "${LONGTERM_SHARE:-${GOLIATH_LONGTERM_SHARE:-}}"
  append_kv "$dest" GOLIATH_LONGTERM_ACCESS_KEY_ID "$long_key"
  append_kv "$dest" GOLIATH_LONGTERM_SECRET_ACCESS_KEY "$long_secret"
  if [[ -n "${GOLIATH_WORKER_CAPABILITY:-}" ]]; then
    append_kv "$dest" WORKER_CAPABILITY "$GOLIATH_WORKER_CAPABILITY"
  fi
  # The worker talks to the gateway. The Neon password stays off the pod
  # unless an operator sets GOLIATH_WORKER_NEEDS_DATABASE=1.
  if [[ "${GOLIATH_WORKER_NEEDS_DATABASE:-}" == "1" ]]; then
    append_kv "$dest" POSTGRES_HOST "${POSTGRES_HOST:-}"
    append_kv "$dest" POSTGRES_DB "${POSTGRES_DB:-}"
    append_kv "$dest" POSTGRES_USER "${POSTGRES_USER:-}"
    append_kv "$dest" POSTGRES_SSLMODE "${POSTGRES_SSLMODE:-require}"
    append_kv "$dest" POSTGRES_CHANNEL_BINDING "${POSTGRES_CHANNEL_BINDING:-require}"
    append_kv "$dest" POSTGRES_PASSWORD "${NEON_TEST_PASSWORD:-${POSTGRES_PASSWORD:-}}"
  fi
  umask "$old_umask"
  chmod 600 "$dest"
  log "wrote $dest"
}

write_token_file() {
  local dest="$1"
  local value="$2"
  local old_umask
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    die "GOLIATH_WORKER_TOKEN contains a newline"
  fi
  install -d -m 755 "$(dirname "$dest")"
  old_umask="$(umask)"
  umask 077
  case "$value" in
    WORKER_ID=*|WORKER_TOKEN=*)
      printf '%s\n' "$value" >"$dest"
      ;;
    *)
      if [[ -n "${WORKER_ID:-}" ]]; then
        printf 'WORKER_ID=%s\nWORKER_TOKEN=%s\n' "$WORKER_ID" "$value" >"$dest"
      else
        printf 'WORKER_TOKEN=%s\n' "$value" >"$dest"
      fi
      ;;
  esac
  umask "$old_umask"
  chmod 600 "$dest"
}

restore_or_write_token() {
  local state persist dest
  state="$(pod_dir)"
  persist="$state/worker-token"
  dest="$METHYL_WORKER_TOKEN_FILE"
  mkdir -p "$state"
  if [[ -f "$persist" ]]; then
    install -d -m 755 "$(dirname "$dest")"
    install -m 600 "$persist" "$dest"
    log "restored worker token to ${dest}"
    return 0
  fi
  if [[ -z "${GOLIATH_WORKER_TOKEN:-}" ]]; then
    log "no persisted worker token; enroll will write ${dest}"
    return 0
  fi
  write_token_file "$dest" "$GOLIATH_WORKER_TOKEN"
  install -m 600 "$dest" "$persist"
  log "wrote worker token to ${dest}"
}

persist_token() {
  local dest persist
  dest="$METHYL_WORKER_TOKEN_FILE"
  persist="$(pod_dir)/worker-token"
  [[ -f "$dest" ]] || die "worker token file ${dest} was not written"
  mkdir -p "$(pod_dir)"
  install -m 600 "$dest" "$persist"
}

stamp_payload() {
  local current
  current="$(readlink -f "$ROOT/current" 2>/dev/null || printf '%s' "$ROOT/current")"
  printf 'api=%s\narch=%s\ncurrent=%s\n' "$WORKER_API_BASE" "$ARCH" "$current"
}

already_joined() {
  local stamp="$1"
  local token="$2"
  local expected
  [[ -f "$stamp" && -f "$token" ]] || return 1
  expected="$(stamp_payload)"
  [[ "$(cat "$stamp")" == "$expected" ]] || return 1
  grep -q '^WORKER_ID=' "$token" || return 1
  grep -q '^WORKER_TOKEN=' "$token" || return 1
}

run_provision() {
  local provision
  provision="$(resolve_tool "${GOLIATH_PROVISION_SCRIPT:-}" provision_worker_node.sh)" \
    || die "provision_worker_node.sh not found under $ROOT/current"
  local -a args=(
    --join-mode join
    --arch "$ARCH"
    --root "$ROOT"
    --cluster "${CLUSTER_KEY:-goliath}"
    --gpu
    --enroll-worker
    --skip-preflight
    --skip-host-setup
    --skip-verify
  )
  if [[ -n "${GOLIATH_WORKER_CAPABILITY:-}" ]]; then
    args+=(--capability "$GOLIATH_WORKER_CAPABILITY")
  fi
  log "provision_worker_node.sh --join-mode join --arch ${ARCH} --gpu (nvidia-smi probe; catalog capability only if GOLIATH_WORKER_CAPABILITY is set)"
  bash "$provision" "${args[@]}"
}

report_worker() {
  local action="$1"
  local reporter msg
  msg="RUNPOD_POD_ID=${RUNPOD_POD_ID:-unset} host=${HOST_SHORT} arch=${ARCH} GPU=${GPU_DESC}"
  reporter="$(resolve_tool "${GOLIATH_REPORT_SCRIPT:-}" report_deploy.sh)" \
    || die "report_deploy.sh not found under $ROOT/current"
  bash "$reporter" "$action" \
    --service goliath-worker \
    --env "$GOLIATH_ENV" \
    --message "$msg" || true
}

systemd_running() {
  command -v systemctl >/dev/null 2>&1 || return 1
  [[ -d /run/systemd/system ]] || return 1
  systemctl show-environment >/dev/null 2>&1
}

worker_bin() {
  local candidate
  for candidate in "$ROOT/venv-${ARCH}/bin/methyl-worker" "$ROOT/venv/bin/methyl-worker"; do
    if [[ -x "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  if command -v methyl-worker >/dev/null 2>&1; then
    command -v methyl-worker
    return 0
  fi
  return 1
}

load_token_env() {
  local file="$1"
  local line key val
  [[ -f "$file" ]] || die "missing worker token file ${file}"
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    case "$key" in
      WORKER_ID) WORKER_ID="$val"; export WORKER_ID ;;
      WORKER_TOKEN) WORKER_TOKEN="$val"; export WORKER_TOKEN ;;
      *) ;;
    esac
  done <"$file"
}

start_supervisor() {
  local bin pidfile token_file
  bin="$(worker_bin)" || {
    log "methyl-worker not found under $ROOT/venv-${ARCH} or $ROOT/venv"
    return 1
  }
  mkdir -p "$LOG_DIR"
  pidfile="$LOG_DIR/methyl-worker.supervisor.pid"
  if [[ -f "$pidfile" ]]; then
    local old
    old="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
      log "methyl-worker supervisor already running pid=${old}"
      return 0
    fi
  fi
  token_file="$METHYL_WORKER_TOKEN_FILE"
  # Monitor mode puts this background loop in its own process group so a
  # later stop (kill -- -pid) also stops methyl-worker.
  set -m
  (
    load_token_env "$token_file"
    while true; do
      "$bin" --api-base "$WORKER_API_BASE" >>"$LOG_DIR/methyl-worker.log" 2>&1 || true
      sleep 5
    done
  ) &
  set +m
  printf '%s\n' "$!" >"$pidfile"
  log "methyl-worker supervisor pid=$(cat "$pidfile") logs=${LOG_DIR}/methyl-worker.log"
}

start_worker() {
  local installer=""
  if systemd_running; then
    installer="$(resolve_tool "" install_worker_systemd.sh)" || installer=""
    if [[ -n "$installer" ]]; then
      local -a sd_args=(--root "$ROOT" --arch "$ARCH")
      if [[ -n "${GOLIATH_WORKER_CAPABILITY:-}" ]]; then
        sd_args+=(--capability "$GOLIATH_WORKER_CAPABILITY")
      else
        sd_args+=(--detect-capabilities)
      fi
      if GOLIATH_ROLLOUTS_EXTERNAL=1 bash "$installer" "${sd_args[@]}"; then
        log "started methyl-worker via systemd"
        return 0
      fi
      log "systemd unit install failed; falling back to a supervisor"
    else
      log "install_worker_systemd.sh not found; falling back to a supervisor"
    fi
  else
    log "systemd is not running; starting methyl-worker under a supervisor"
  fi
  start_supervisor
}

finish_exec() {
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    log "GOLIATH_START_NO_EXEC set; not execing /start.sh"
    exit 0
  fi
  if [[ -x /start.sh ]]; then
    log "exec /start.sh"
    exec /start.sh "$@"
  fi
  log "ERROR: /start.sh is missing; keeping the worker supervisor in the foreground"
  local pidfile="$LOG_DIR/methyl-worker.supervisor.pid"
  if [[ -f "$pidfile" ]]; then
    wait "$(cat "$pidfile")" || true
  fi
  exit 1
}

main() {
  wait_for_work
  mkdir -p "$ROOT" "$ROOT/tmp"
  ensure_current
  maybe_reexec "$@"
  [[ -n "${WORKER_API_BASE:-}" ]] || die "WORKER_API_BASE is required (profile public_base_url, for example https://45.76.21.8/v1)"
  detect_gpu
  HOST_SHORT="$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)"
  restore_or_write_token
  write_worker_env
  local state stamp
  state="$(pod_dir)"
  stamp="$state/joined"
  mkdir -p "$state"
  if already_joined "$stamp" "$METHYL_WORKER_TOKEN_FILE"; then
    log "already joined this release; skipping provision_worker_node.sh"
  else
    run_provision
    persist_token
    stamp_payload >"$stamp"
    chmod 600 "$stamp"
  fi
  report_worker start
  if ! start_worker; then
    report_worker failed
    die "worker failed to start"
  fi
  report_worker succeeded
  finish_exec "$@"
}

main "$@"
