#!/usr/bin/env bash
# RunPod container start command for a self-provisioning goliath worker.
#
# The stock runpod-torch-v280 image starts /start.sh (Jupyter and SSH). The
# template start command downloads deploy/runpod/goliath-worker-bootstrap.sh
# from the goliathomics release and runs it. The bootstrap installs this
# script onto /work and runs it. This script joins the gateway,
# starts methyl-worker, then execs /start.sh.
#
# Secrets are read only from the environment (RunPod secrets). This script
# never prints them. Do not run it under set -x.
#
# A fresh /work only needs to be mounted and writable. This script creates
# the shared layout, downloads the release bundle when current is missing,
# and joins the gateway. Failures log and exec /start.sh so sshd (port 22)
# and Jupyter (port 8888) stay up. RunPod then does not restart the pod.
# GOLIATH_START_NO_EXEC=1 makes those failures exit 1 instead (tests).

set +x
set -euo pipefail
set -E

ROOT="${GOLIATH_ROOT:-/work/goliath}"
WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}"
ARCH="${GOLIATH_WORKER_ARCH:-amd64}"
LOG_DIR="${GOLIATH_LOG_DIR:-$ROOT/logs}"
export METHYL_WORKER_TOKEN_FILE="${METHYL_WORKER_TOKEN_FILE:-/etc/methyl/worker-token}"
export GOLIATH_ENV="${GOLIATH_ENV:-goliath-test}"

GPU_DESC=""
HOST_SHORT=""

log() { printf '[goliath-worker-start] %s\n' "$*"; }

exec_keepalive() {
  local keepalive="${GOLIATH_START_KEEPALIVE:-/start.sh}"
  if [[ -x "$keepalive" ]]; then
    log "exec ${keepalive}"
    if [[ "$#" -gt 0 ]]; then
      exec "$keepalive" "$@"
    fi
    exec "$keepalive"
  fi
  printf '[goliath-worker-start] ERROR: %s is not executable; sleeping so the pod is not restarted\n' "$keepalive" >&2
  exec sleep infinity
}

die() {
  trap - ERR
  printf '[goliath-worker-start] ERROR: %s\n' "$*" >&2
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    exit 1
  fi
  printf '[goliath-worker-start] keeping the pod alive for debugging (sshd port 22, Jupyter port 8888)\n' >&2
  exec_keepalive
}

on_err() {
  local rc=$?
  trap - ERR
  printf '[goliath-worker-start] ERROR: command failed at line %s (exit %s)\n' "$1" "$rc" >&2
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    exit 1
  fi
  printf '[goliath-worker-start] keeping the pod alive for debugging (sshd port 22, Jupyter port 8888)\n' >&2
  exec_keepalive
}
trap 'on_err "${BASH_LINENO[0]}"' ERR

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

# samples, genomes, and the docker data-root live on the network volume.
# Join mode skips host setup, so create them here. init_work_layout.sh only
# creates missing roots and keeps an existing site manifest.
ensure_work_layout() {
  local work_root init
  work_root="$(dirname "$ROOT")"
  init=""
  if init="$(resolve_tool "" init_work_layout.sh)"; then
    log "ensuring work layout under ${work_root}"
    bash "$init" --work "$work_root" || die "init_work_layout.sh failed"
  else
    log "init_work_layout.sh not in the release; creating samples, genomes, and goliath directories"
    mkdir -p \
      "$work_root/samples" \
      "$work_root/projects" \
      "$work_root/cache" \
      "$work_root/genomes" \
      "$work_root/site" \
      "$ROOT/bin" \
      "$ROOT/env" \
      "$ROOT/logs" \
      "$ROOT/tmp" \
      "$ROOT/releases"
    chmod 0777 "$work_root/samples" "$work_root/projects" "$work_root/cache" 2>/dev/null || true
    chmod 0755 "$work_root/genomes" "$work_root/site" "$ROOT" 2>/dev/null || true
  fi
  mkdir -p "$ROOT/docker" "$ROOT/logs" "$ROOT/tmp" "$ROOT/bin"
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

system_ca_bundle() {
  local candidate
  if [[ -n "${GOLIATH_SYSTEM_CA_BUNDLE:-}" && -f "${GOLIATH_SYSTEM_CA_BUNDLE}" ]]; then
    printf '%s' "${GOLIATH_SYSTEM_CA_BUNDLE}"
    return 0
  fi
  for candidate in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do
    if [[ -f "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

write_gateway_ca_pem() {
  local dest="$1"
  local pem="$2"
  local old_umask
  if [[ "$pem" != *$'\n'* && "$pem" == *'\n'* ]]; then
    pem="${pem//\\n/$'\n'}"
  fi
  if [[ "$pem" != *"BEGIN CERTIFICATE"* ]]; then
    die "GOLIATH_GATEWAY_CA_PEM does not contain a PEM certificate"
  fi
  mkdir -p "$(dirname "$dest")"
  old_umask="$(umask)"
  umask 022
  printf '%s' "$pem" >"$dest"
  if [[ "$pem" != *$'\n' ]]; then
    printf '\n' >>"$dest"
  fi
  umask "$old_umask"
  chmod 644 "$dest"
}

# Persist the gateway trust anchor and point requests/OpenSSL at a bundle that
# still contains the system CAs. GOLIATH_GATEWAY_CA_PEM is the RunPod secret
# (the PEM text). It is removed from the environment after the file is written.
install_gateway_ca() {
  local pem dest system bundle
  pem="${GOLIATH_GATEWAY_CA_PEM:-}"
  dest="${GOLIATH_GATEWAY_CA_FILE:-$ROOT/env/gateway-ca.pem}"
  if [[ -n "$pem" ]]; then
    write_gateway_ca_pem "$dest" "$pem"
    unset GOLIATH_GATEWAY_CA_PEM
    export GOLIATH_GATEWAY_CA_FILE="$dest"
  elif [[ -n "${GOLIATH_GATEWAY_CA_FILE:-}" && -f "$GOLIATH_GATEWAY_CA_FILE" ]]; then
    dest="$GOLIATH_GATEWAY_CA_FILE"
  elif [[ -f "$ROOT/env/gateway-ca.pem" ]]; then
    dest="$ROOT/env/gateway-ca.pem"
    export GOLIATH_GATEWAY_CA_FILE="$dest"
  else
    return 0
  fi
  export GOLIATH_GATEWAY_CA_FILE="$dest"
  bundle="$ROOT/env/requests-ca-bundle.pem"
  system="$(system_ca_bundle || true)"
  if [[ -n "$system" ]]; then
    {
      cat "$system"
      [[ "$(tail -c 1 "$system" 2>/dev/null || true)" == $'\n' ]] || printf '\n'
      cat "$dest"
    } >"$bundle"
    chmod 644 "$bundle"
    if [[ -z "${REQUESTS_CA_BUNDLE:-}" ]]; then
      export REQUESTS_CA_BUNDLE="$bundle"
    fi
    if [[ -z "${SSL_CERT_FILE:-}" ]]; then
      export SSL_CERT_FILE="$bundle"
    fi
    log "gateway CA file ${dest}; requests bundle ${REQUESTS_CA_BUNDLE}"
  else
    log "gateway CA file ${dest}; system CA bundle not found, so REQUESTS_CA_BUNDLE was left unset"
  fi
}

write_worker_env() {
  local dest="$ROOT/env/worker.env"
  local old_umask tmp owned
  local b2_key="${B2_ACCESS_KEY_ID:-${AWS_ACCESS_KEY_ID:-}}"
  local b2_secret="${B2_SECRET_ACCESS_KEY:-${AWS_SECRET_ACCESS_KEY:-}}"
  local b2_endpoint="${B2_ENDPOINT:-${AWS_ENDPOINT_URL:-}}"
  local b2_bucket="${B2_BUCKET:-${GOLIATH_SHARED_BUCKET:-}}"
  local long_key="${LONGTERM_ACCESS_KEY_ID:-${GOLIATH_LONGTERM_ACCESS_KEY_ID:-}}"
  local long_secret="${LONGTERM_SECRET_ACCESS_KEY:-${GOLIATH_LONGTERM_SECRET_ACCESS_KEY:-}}"
  local long_endpoint="${LONGTERM_ENDPOINT:-${GOLIATH_LONGTERM_ENDPOINT:-}}"
  # promote_release.sh already wrote extractor, Parabricks, PATH, CAAS, and
  # HBM settings. Refresh only the storage and API keys this script owns.
  owned='WORKER_API_BASE|GOLIATH_ENV|GOLIATH_SHARED_PROVIDER|GOLIATH_SHARED_BUCKET|AWS_ENDPOINT_URL|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|GOLIATH_LONGTERM_PROVIDER|GOLIATH_LONGTERM_BUCKET|GOLIATH_LONGTERM_ENDPOINT|GOLIATH_LONGTERM_HOST|GOLIATH_LONGTERM_SHARE|GOLIATH_LONGTERM_ACCESS_KEY_ID|GOLIATH_LONGTERM_SECRET_ACCESS_KEY|WORKER_CAPABILITY|POSTGRES_HOST|POSTGRES_DB|POSTGRES_USER|POSTGRES_SSLMODE|POSTGRES_CHANNEL_BINDING|POSTGRES_PASSWORD|GOLIATH_GATEWAY_CA_FILE|REQUESTS_CA_BUNDLE|SSL_CERT_FILE'
  mkdir -p "$ROOT/env"
  old_umask="$(umask)"
  umask 077
  tmp="$(mktemp "${dest}.XXXXXX")"
  if [[ -f "$dest" ]]; then
    grep -v -E "^(${owned})=" "$dest" >"$tmp" || true
  fi
  if [[ -s "$tmp" && "$(tail -c 1 "$tmp" || true)" != $'\n' ]]; then
    printf '\n' >>"$tmp"
  fi
  append_kv "$tmp" WORKER_API_BASE "${WORKER_API_BASE:-}"
  append_kv "$tmp" GOLIATH_ENV "$GOLIATH_ENV"
  append_kv "$tmp" GOLIATH_GATEWAY_CA_FILE "${GOLIATH_GATEWAY_CA_FILE:-}"
  append_kv "$tmp" REQUESTS_CA_BUNDLE "${REQUESTS_CA_BUNDLE:-}"
  append_kv "$tmp" SSL_CERT_FILE "${SSL_CERT_FILE:-}"
  append_kv "$tmp" GOLIATH_SHARED_PROVIDER "b2"
  append_kv "$tmp" GOLIATH_SHARED_BUCKET "$b2_bucket"
  append_kv "$tmp" AWS_ENDPOINT_URL "$b2_endpoint"
  append_kv "$tmp" AWS_ACCESS_KEY_ID "$b2_key"
  append_kv "$tmp" AWS_SECRET_ACCESS_KEY "$b2_secret"
  append_kv "$tmp" GOLIATH_LONGTERM_PROVIDER "myqnapcloud"
  append_kv "$tmp" GOLIATH_LONGTERM_BUCKET "${LONGTERM_BUCKET:-${GOLIATH_LONGTERM_BUCKET:-}}"
  append_kv "$tmp" GOLIATH_LONGTERM_ENDPOINT "$long_endpoint"
  append_kv "$tmp" GOLIATH_LONGTERM_HOST "${LONGTERM_HOST:-${GOLIATH_LONGTERM_HOST:-}}"
  append_kv "$tmp" GOLIATH_LONGTERM_SHARE "${LONGTERM_SHARE:-${GOLIATH_LONGTERM_SHARE:-}}"
  append_kv "$tmp" GOLIATH_LONGTERM_ACCESS_KEY_ID "$long_key"
  append_kv "$tmp" GOLIATH_LONGTERM_SECRET_ACCESS_KEY "$long_secret"
  if [[ -n "${GOLIATH_WORKER_CAPABILITY:-}" ]]; then
    append_kv "$tmp" WORKER_CAPABILITY "$GOLIATH_WORKER_CAPABILITY"
  fi
  # The worker talks to the gateway. The Neon password stays off the pod
  # unless an operator sets GOLIATH_WORKER_NEEDS_DATABASE=1.
  if [[ "${GOLIATH_WORKER_NEEDS_DATABASE:-}" == "1" ]]; then
    append_kv "$tmp" POSTGRES_HOST "${POSTGRES_HOST:-}"
    append_kv "$tmp" POSTGRES_DB "${POSTGRES_DB:-}"
    append_kv "$tmp" POSTGRES_USER "${POSTGRES_USER:-}"
    append_kv "$tmp" POSTGRES_SSLMODE "${POSTGRES_SSLMODE:-require}"
    append_kv "$tmp" POSTGRES_CHANNEL_BINDING "${POSTGRES_CHANNEL_BINDING:-require}"
    append_kv "$tmp" POSTGRES_PASSWORD "${NEON_TEST_PASSWORD:-${POSTGRES_PASSWORD:-}}"
  fi
  mv -f "$tmp" "$dest"
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
  # worker.env sets TMPDIR=/var/tmp/methyl-samtools. The systemd unit creates
  # that directory in ExecStartPre; RunPod has no unit, so tempfile calls fail
  # on the first job unless the supervisor creates it too.
  mkdir -p /var/tmp/methyl-samtools
  chmod 1777 /var/tmp/methyl-samtools 2>/dev/null || true
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
    # RunPod has no systemd. Load the same worker.env EnvironmentFile the
    # unit would, then the token so WORKER_ID / WORKER_TOKEN win.
    if [[ -f "$ROOT/env/worker.env" ]]; then
      set -a
      # shellcheck disable=SC1091
      source "$ROOT/env/worker.env"
      set +a
    fi
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
  exec_keepalive "$@"
}

main() {
  wait_for_work
  mkdir -p "$ROOT" "$ROOT/tmp"
  ensure_current
  maybe_reexec "$@"
  ensure_work_layout
  [[ -n "${WORKER_API_BASE:-}" ]] || die "WORKER_API_BASE is required (profile public_base_url, for example https://45.76.21.8/v1)"
  detect_gpu
  install_gateway_ca
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
