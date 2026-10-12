#!/usr/bin/env bash
# Deploy one customer target described by deploy/targets/<name>.json.
# Secret values are read from the environment (GitHub environment secrets) or
# /etc/goliath/<target>.env. They are written to per-host env files over SSH
# and are never printed.
#
# Workers with self_provision=true (a RunPod pool) are not reached over SSH.
# deploy_target.sh prints how those pods boot themselves. The pod start
# script is deploy/runpod/goliath-worker-start.sh.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/deploy_target.sh --target NAME [--component gateway|workers|runtime|schema|all] [--dry-run]

Options:
  --target NAME       Profile deploy/targets/<name>.json
                      (example profiles are allowed only with --dry-run)
  --component NAME    gateway, workers, runtime, schema, or all (default: all)
  --dry-run           Print hosts and secret *names*. Do not SSH or print values.
  -h, --help

GOLIATH_ENV is set from the profile rollouts_environment before any install
script runs, so report_deploy.sh files the events on that environment.

Workers marked self_provision do not use SSH. --component workers and
--component all print the pod template and return without opening a session.
Schema apply uses database.schema_host when it is set (Neon direct host),
and database.host otherwise (the pooled host the gateway keeps using).
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

TARGET=""
COMPONENT="all"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --component) COMPONENT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$TARGET" ]] || die "--target is required"
case "$COMPONENT" in
  gateway|workers|runtime|schema|all) ;;
  *) die "Unknown component: $COMPONENT" ;;
esac
command -v jq >/dev/null 2>&1 || die "jq is required"

PROFILE="$REPO_ROOT/deploy/targets/${TARGET}.json"
if [[ ! -f "$PROFILE" ]]; then
  example="$REPO_ROOT/deploy/targets/${TARGET}.example.json"
  if [[ -f "$example" && "$DRY_RUN" -eq 1 ]]; then
    PROFILE="$example"
    info "Using example profile $PROFILE"
  elif [[ -f "$example" ]]; then
    die "Refusing to deploy example profile. Copy it to deploy/targets/${TARGET}.json"
  else
    die "Target profile not found: deploy/targets/${TARGET}.json"
  fi
fi

jq -e . "$PROFILE" >/dev/null || die "Invalid JSON: $PROFILE"

load_target_env() {
  local file="/etc/goliath/${TARGET}.env"
  [[ -r "$file" ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && -z "${!key:-}" ]]; then
      val="${val%\"}"
      val="${val#\"}"
      printf -v "$key" '%s' "$val"
      export "${key?}"
    fi
  done <"$file"
}

load_target_env

resolve_ref() {
  local raw="$1"
  if [[ "$raw" == "null" || -z "$raw" ]]; then
    printf ''
    return 0
  fi
  if [[ "$raw" == secret:* ]]; then
    local name="${raw#secret:}"
    if [[ ! "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      echo "[ERROR] invalid secret reference" >&2
      return 1
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
      printf 'secret:%s' "$name"
      return 0
    fi
    if [[ -z "${!name:-}" ]]; then
      echo "[ERROR] unset secret:${name} (environment or /etc/goliath/${TARGET}.env)" >&2
      return 1
    fi
    printf '%s' "${!name}"
    return 0
  fi
  if [[ "$raw" == *secret:* ]]; then
    echo "[ERROR] secret values must be references like secret:NAME, not embedded" >&2
    return 1
  fi
  printf '%s' "$raw"
}

jq_get() {
  jq -r "$1" "$PROFILE"
}

export GOLIATH_ENV
GOLIATH_ENV="$(jq_get '.rollouts_environment')"
[[ -n "$GOLIATH_ENV" && "$GOLIATH_ENV" != "null" ]] || die "rollouts_environment is required"
info "target=${TARGET} component=${COMPONENT} rollouts_environment=${GOLIATH_ENV}"

secret_names() {
  jq -r '
    [.. | strings | select(startswith("secret:"))] | unique | .[]
  ' "$PROFILE"
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "secret names (values are not read):"
  secret_names | sed 's/^/  /'
  info "gateway hosts: $(jq_get '[.gateway.hosts[]?.ssh] | join(", ")')"
  info "workers: $(jq_get '[.workers[]? | if .self_provision == true then "\(.name) self_provision \(.provider // "unknown") \(.arch) \(.template // "")" else "\(.name) \(.ssh // "") \(.arch) \(.capability // "")" end] | join("; ")')"
  info "database host: $(jq_get '.database.host') schema_host=$(jq_get '.database.schema_host // ""') kind=$(jq_get '.database.kind') channel_binding=$(jq_get '.database.channel_binding // ""')"
  info "shared storage: $(jq_get '.shared_storage.provider // "none"') $(jq_get '.shared_storage.endpoint // ""') bucket=$(jq_get '.shared_storage.bucket // ""')"
  info "long-term storage: $(jq_get '.long_term_storage.provider // "none"') endpoint=$(jq_get '.long_term_storage.endpoint // ""') roles=$(jq_get '[.long_term_storage.roles[]?] | join(",")')"
  if jq -e '[.. | objects | .identity_file? // empty | select(. != "")] | length > 0' "$PROFILE" >/dev/null; then
    info "ssh auth: publickey only (PasswordAuthentication=no) when identity_file is set"
  fi
  exit 0
fi

assert_remote_literal() {
  local label="$1"
  local value="$2"
  if [[ "$value" == *\'* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    die "${label} contains a quote or newline and cannot be sent to a remote shell"
  fi
}

# identity_file may be a filesystem path or secret:NAME. A secret value is the
# PEM (or OpenSSH) private key. It is written mode 600 under the stage dir
# and never printed. GitHub sometimes stores newlines as the two characters \n.
materialize_identity() {
  local raw="$1"
  if [[ -z "$raw" || "$raw" == "null" ]]; then
    printf ''
    return 0
  fi
  if [[ "$raw" == secret:* ]]; then
    local name value file old_umask
    name="${raw#secret:}"
    value="$(resolve_ref "$raw")" || return 1
    if [[ "$value" != *$'\n'* && "$value" == *'\n'* ]]; then
      value="${value//\\n/$'\n'}"
    fi
    file="$stage_dir/ssh-identity-${name}"
    old_umask="$(umask)"
    umask 077
    printf '%s' "$value" >"$file"
    if [[ "$value" != *$'\n' ]]; then
      printf '\n' >>"$file"
    fi
    umask "$old_umask"
    chmod 600 "$file"
    printf '%s' "$file"
    return 0
  fi
  printf '%s' "$raw"
}

ssh_cmd() {
  local dest="$1"
  shift
  local port identity raw_identity
  port="$(jq -r --arg dest "$dest" '.. | objects | select(.ssh? == $dest) | .port // empty' "$PROFILE" | head -n 1)"
  raw_identity="$(jq -r --arg dest "$dest" '.. | objects | select(.ssh? == $dest) | .identity_file // empty' "$PROFILE" | head -n 1)"
  identity="$(materialize_identity "$raw_identity")" || return 1
  local args=(-o BatchMode=yes -o ConnectTimeout=20)
  if [[ -n "$port" ]]; then
    args+=(-p "$port")
  fi
  if [[ -n "$identity" ]]; then
    # The gateway address is public. Refuse password auth even if sshd allows it.
    args+=(
      -i "$identity"
      -o IdentitiesOnly=yes
      -o PreferredAuthentications=publickey
      -o PasswordAuthentication=no
    )
  fi
  ssh "${args[@]}" "$dest" "$@"
}

scp_to() {
  local dest="$1"
  local src="$2"
  local remote="$3"
  ssh_cmd "$dest" "cat > '$remote'" <"$src"
}

write_kv_file() {
  local dest="$1"
  shift
  umask 077
  : >"$dest"
  local key val
  while [[ $# -ge 2 ]]; do
    key="$1"
    val="$2"
    shift 2
    # Double-quote so '#', spaces, and quotes survive systemd EnvironmentFile.
    val="${val//\\/\\\\}"
    val="${val//\"/\\\"}"
    val="${val//$'\n'/}"
    printf '%s="%s"\n' "$key" "$val" >>"$dest"
  done
  chmod 600 "$dest"
}

db_kind="$(jq_get '.database.kind // "postgres"')"
db_host="$(jq_get '.database.host')"
db_schema_host="$(jq_get '.database.schema_host // empty')"
if [[ -z "$db_schema_host" || "$db_schema_host" == "null" ]]; then
  db_schema_host="$db_host"
fi
db_port="$(jq_get '.database.port // 5432')"
db_name="$(jq_get '.database.dbname')"
db_user="$(jq_get '.database.user')"
db_ssl="$(jq_get '.database.sslmode // "require"')"
db_channel="$(jq_get '.database.channel_binding // empty')"
if [[ "$db_channel" == "null" ]]; then
  db_channel=""
fi

load_db_password() {
  if [[ -n "${DB_PASSWORD+x}" ]]; then
    return 0
  fi
  DB_PASSWORD="$(resolve_ref "$(jq_get '.database.password // empty')")"
}

shared_provider="$(jq_get '.shared_storage.provider // "none"')"
shared_bucket="$(jq_get '.shared_storage.bucket // empty')"
shared_endpoint="$(jq_get '.shared_storage.endpoint // empty')"
shared_key=""
shared_secret=""

long_provider="$(jq_get '.long_term_storage.provider // "none"')"
long_bucket="$(jq_get '.long_term_storage.bucket // empty')"
long_endpoint="$(jq_get '.long_term_storage.endpoint // empty')"
long_host="$(jq_get '.long_term_storage.host // empty')"
long_share="$(jq_get '.long_term_storage.share // empty')"
long_key=""
long_secret=""

load_storage_secrets() {
  if [[ "${STORAGE_SECRETS_LOADED:-}" == "1" ]]; then
    return 0
  fi
  shared_key="$(resolve_ref "$(jq_get '.shared_storage.access_key_id // empty')")"
  shared_secret="$(resolve_ref "$(jq_get '.shared_storage.secret_access_key // empty')")"
  long_key="$(resolve_ref "$(jq_get '.long_term_storage.access_key_id // empty')")"
  long_secret="$(resolve_ref "$(jq_get '.long_term_storage.secret_access_key // empty')")"
  STORAGE_SECRETS_LOADED=1
}

gateway_url="$(jq_get '.gateway.public_base_url // empty')"

stage_dir="$(mktemp -d)"
chmod 700 "$stage_dir"
cleanup() {
  local rc=$?
  rm -rf "$stage_dir"
  exit "$rc"
}
trap cleanup EXIT

report_component() {
  local action="$1"
  local service="$2"
  local message="$3"
  if rollouts_service_skipped "$service"; then
    return 0
  fi
  rollouts_report "$action" --service "$service" --env "$GOLIATH_ENV" --message "$message"
}

run_schema() {
  local apply
  apply="$(jq_get '.schema.apply // "test-bed"')"
  if [[ "$apply" == "none" ]]; then
    info "schema.apply=none; skipping SQL"
    return 0
  fi
  load_db_password
  report_component start goliath-db-schema "schema ${TARGET}"
  # schema_host is the non-pooled Neon endpoint. The gateway keeps using host.
  # PGDATABASE is a libpq parameter, so the mixed-case name GoliathOmics is
  # passed as-is. Inside SQL that identifier must be double-quoted: "GoliathOmics".
  info "schema apply host=${db_schema_host} (gateway host=${db_host})"
  export PGHOST="$db_schema_host" PGPORT="$db_port" PGDATABASE="$db_name" PGUSER="$db_user"
  export PGSSLMODE="$db_ssl" PGPASSWORD="$DB_PASSWORD"
  if [[ -n "$db_channel" ]]; then
    export PGCHANNELBINDING="$db_channel"
  fi
  export GOLIATH_DEPLOY_TARGET="$TARGET"
  if GOLIATH_ROLLOUTS_EXTERNAL=1 bash "$REPO_ROOT/workflow_engine/sql_pg/deploy_test_bed.sh"; then
    report_component succeeded goliath-db-schema "schema ${TARGET}"
  else
    report_component failed goliath-db-schema "schema ${TARGET} failed"
    return 1
  fi
}

install_remote_tree() {
  local dest="$1"
  local bundle="$stage_dir/bundle.tgz"
  tar -C "$REPO_ROOT" -czf "$bundle" \
    scripts/report_deploy.sh \
    scripts/rollouts_hook.sh \
    scripts/install_gateway_systemd.sh \
    scripts/install_worker_systemd.sh \
    scripts/install_reclaim_leases_timer.sh \
    scripts/promote_release.sh \
    scripts/install_release.sh \
    scripts/write_worker_env.sh \
    scripts/setup_gpu_node.sh \
    scripts/verify_setup.sh \
    scripts/verify_e2e_node.sh \
    scripts/bootstrap_goliath.sh \
    scripts/detect_platform.sh \
    scripts/platform_matrix.env \
    scripts/install_methyl_extractor_tarball.sh \
    deploy/systemd \
    deploy/nginx
  scp_to "$dest" "$bundle" /tmp/goliath-deploy-bundle.tgz
  ssh_cmd "$dest" "rm -rf /tmp/goliath-deploy && mkdir -p /tmp/goliath-deploy && tar -xzf /tmp/goliath-deploy-bundle.tgz -C /tmp/goliath-deploy && rm -f /tmp/goliath-deploy-bundle.tgz"
}

run_gateway() {
  local count
  count="$(jq_get '.gateway.hosts | length')"
  if [[ "$count" == "0" || "$count" == "null" ]]; then
    if [[ "$COMPONENT" == "gateway" ]]; then
      die "gateway.hosts is empty"
    fi
    info "no gateway hosts; skipping gateway"
    return 0
  fi
  load_db_password
  local i dest envfile remote_env root user port bind
  root="$(jq_get '.gateway.install_root // "/opt/methyl-gateway"')"
  user="$(jq_get '.gateway.user // "goliath"')"
  port="$(jq_get '.gateway.port // 8080')"
  bind="$(jq_get '.gateway.bind_host // "127.0.0.1"')"
  remote_env="$(jq_get '.gateway.env_file // empty')"
  for ((i = 0; i < count; i++)); do
    dest="$(jq_get ".gateway.hosts[$i].ssh")"
    assert_remote_literal "gateway ssh" "$dest"
    assert_remote_literal "gateway root" "$root"
    assert_remote_literal "gateway user" "$user"
    assert_remote_literal "gateway env file" "$remote_env"
    assert_remote_literal "rollouts environment" "$GOLIATH_ENV"
    info "gateway host $dest"
    report_component start goliath-gateway "gateway ${dest}"
    # install_gateway_systemd.sh also installs the reclaim timer. EXTERNAL skips
    # the nested reports, so the driver records both services for this host.
    report_component start goliath-reclaim-leases "reclaim-leases ${dest}"
    envfile="$stage_dir/gateway.env"
    if [[ "$db_kind" == "mssql" ]]; then
      write_kv_file "$envfile" \
        BACKEND_DB mssql \
        AZURE_SQL_SERVER "$db_host" \
        AZURE_SQL_DB "$db_name" \
        AZURE_SQL_USER "$db_user" \
        AZURE_SQL_PASSWORD "$DB_PASSWORD" \
        WF_GATEWAY_HOST "$bind" \
        WF_GATEWAY_PORT "$port" \
        GOLIATH_ENV "$GOLIATH_ENV"
    else
      local -a gw_pairs=(
        BACKEND_DB postgres
        POSTGRES_HOST "$db_host"
        POSTGRES_PORT "$db_port"
        POSTGRES_DB "$db_name"
        POSTGRES_USER "$db_user"
        POSTGRES_PASSWORD "$DB_PASSWORD"
        POSTGRES_SSLMODE "$db_ssl"
      )
      if [[ -n "$db_channel" ]]; then
        gw_pairs+=(POSTGRES_CHANNEL_BINDING "$db_channel")
      fi
      gw_pairs+=(
        WF_GATEWAY_HOST "$bind"
        WF_GATEWAY_PORT "$port"
        GOLIATH_ENV "$GOLIATH_ENV"
      )
      write_kv_file "$envfile" "${gw_pairs[@]}"
    fi
    install_remote_tree "$dest"
    scp_to "$dest" "$envfile" /tmp/goliath-gateway.env
    rm -f "$envfile"
    # methyl-gateway.service reads <root>/env/gateway.env. The unit has no
    # --env-file or User= flag. An extra profile env_file is copied beside it.
    local gw_env_cmd="sudo mkdir -p '$root/env' && sudo install -m 600 -o root /tmp/goliath-gateway.env '$root/env/gateway.env'"
    if [[ -n "$remote_env" && "$remote_env" != "null" && "$remote_env" != "$root/env/gateway.env" ]]; then
      gw_env_cmd+=" && sudo install -m 600 -o root /tmp/goliath-gateway.env '$remote_env'"
    fi
    gw_env_cmd+=" && rm -f /tmp/goliath-gateway.env && sudo env GOLIATH_ENV='$GOLIATH_ENV' GOLIATH_ROLLOUTS_EXTERNAL=1 bash /tmp/goliath-deploy/scripts/install_gateway_systemd.sh --root '$root'"
    if ssh_cmd "$dest" "$gw_env_cmd"; then
      report_component succeeded goliath-gateway "gateway ${dest}"
      report_component succeeded goliath-reclaim-leases "reclaim-leases ${dest}"
    else
      report_component failed goliath-gateway "gateway ${dest} failed"
      report_component failed goliath-reclaim-leases "reclaim-leases ${dest} failed"
      return 1
    fi
  done
}

worker_env_file() {
  local dest_file="$1"
  local capability="$2"
  write_kv_file "$dest_file" \
    WORKER_API_BASE "$gateway_url" \
    WORKER_CAPABILITY "$capability" \
    GOLIATH_ENV "$GOLIATH_ENV" \
    GOLIATH_SHARED_PROVIDER "$shared_provider" \
    GOLIATH_SHARED_BUCKET "$shared_bucket" \
    AWS_ENDPOINT_URL "$shared_endpoint" \
    AWS_ACCESS_KEY_ID "$shared_key" \
    AWS_SECRET_ACCESS_KEY "$shared_secret" \
    GOLIATH_LONGTERM_PROVIDER "$long_provider" \
    GOLIATH_LONGTERM_BUCKET "$long_bucket" \
    GOLIATH_LONGTERM_ENDPOINT "$long_endpoint" \
    GOLIATH_LONGTERM_HOST "$long_host" \
    GOLIATH_LONGTERM_SHARE "$long_share" \
    GOLIATH_LONGTERM_ACCESS_KEY_ID "$long_key" \
    GOLIATH_LONGTERM_SECRET_ACCESS_KEY "$long_secret"
}

describe_self_provision_worker() {
  local i="$1"
  local purpose="$2"
  local name provider arch template image mount gpu vcpu ram root
  name="$(jq -r --argjson i "$i" '.workers[$i].name' "$PROFILE")"
  provider="$(jq -r --argjson i "$i" '.workers[$i].provider // "unknown"' "$PROFILE")"
  arch="$(jq -r --argjson i "$i" '.workers[$i].arch' "$PROFILE")"
  template="$(jq -r --argjson i "$i" '.workers[$i].template // ""' "$PROFILE")"
  image="$(jq -r --argjson i "$i" '.workers[$i].image // ""' "$PROFILE")"
  mount="$(jq -r --argjson i "$i" '.workers[$i].volume_mount // "/work"' "$PROFILE")"
  vcpu="$(jq -r --argjson i "$i" '.workers[$i].vcpu // ""' "$PROFILE")"
  ram="$(jq -r --argjson i "$i" '.workers[$i].memory_gb // ""' "$PROFILE")"
  root="$(jq -r --argjson i "$i" '.workers[$i].install_root // "/work/goliath"' "$PROFILE")"
  gpu="$(jq -r --argjson i "$i" '
    .workers[$i].gpu
    | if type == "object" then
        "\(.model) x\(.count // 1) (\(.memory_gb // "?")GB)"
      elif . == true then "yes"
      elif . == false then "no"
      else "unspecified"
      end
  ' "$PROFILE")"
  info "self-provision worker ${name} (${purpose}): no SSH"
  info "  provider=${provider} arch=${arch} template=${template}"
  info "  image=${image}"
  info "  gpu=${gpu} vcpu=${vcpu} memory_gb=${ram} volume=${mount} install_root=${root}"
  info "  boot: deploy/runpod/goliath-worker-start.sh as the pod template start command"
  info "  the pod enrolls with WORKER_API_BASE and reports goliath-worker (RUNPOD_POD_ID, host, arch, GPU)"
}

run_workers() {
  local count
  count="$(jq_get '.workers | length')"
  [[ "$count" != "0" && "$count" != "null" ]] || die "workers is empty"
  local i dest arch cap root host msg self ssh_any=0 self_any=0
  for ((i = 0; i < count; i++)); do
    self="$(jq -r --argjson i "$i" '.workers[$i].self_provision // false' "$PROFILE")"
    if [[ "$self" == "true" ]]; then
      describe_self_provision_worker "$i" "workers"
      self_any=1
      continue
    fi
    ssh_any=1
    load_storage_secrets
    dest="$(jq_get ".workers[$i].ssh")"
    arch="$(jq_get ".workers[$i].arch")"
    cap="$(jq_get ".workers[$i].capability // empty")"
    root="$(jq -r --argjson i "$i" '.workers[$i].install_root // "/work/goliath"' "$PROFILE")"
    if [[ -z "$dest" || "$dest" == "null" ]]; then
      die "worker $(jq_get ".workers[$i].name") has no ssh host; set self_provision true for pools that boot themselves"
    fi
    assert_remote_literal "worker ssh" "$dest"
    assert_remote_literal "worker arch" "$arch"
    assert_remote_literal "worker capability" "$cap"
    assert_remote_literal "worker root" "$root"
    assert_remote_literal "rollouts environment" "$GOLIATH_ENV"
    host="${dest##*@}"
    msg="host=${host} arch=${arch} capability=${cap:-unspecified}"
    info "worker $dest $msg"
    report_component start goliath-worker "$msg"
    local envfile="$stage_dir/worker.env"
    worker_env_file "$envfile" "$cap"
    install_remote_tree "$dest"
    scp_to "$dest" "$envfile" /tmp/goliath-worker.env
    rm -f "$envfile"
    # methyl-worker.service hardcodes /work/goliath/env/worker.env. Also write
    # <root>/env/worker.env when the profile root differs.
    local cap_flag=""
    if [[ -n "$cap" && "$cap" != "null" ]]; then
      cap_flag="--capability '$cap'"
    fi
    if ssh_cmd "$dest" "sudo mkdir -p '$root/env' /work/goliath/env && sudo install -m 600 /tmp/goliath-worker.env '$root/env/worker.env' && sudo install -m 600 /tmp/goliath-worker.env /work/goliath/env/worker.env && rm -f /tmp/goliath-worker.env && sudo env GOLIATH_ENV='$GOLIATH_ENV' GOLIATH_ROLLOUTS_EXTERNAL=1 bash /tmp/goliath-deploy/scripts/install_worker_systemd.sh --root '$root' --arch '$arch' $cap_flag"; then
      report_component succeeded goliath-worker "$msg"
    else
      report_component failed goliath-worker "$msg failed"
      return 1
    fi
  done
  if [[ "$ssh_any" -eq 0 && "$self_any" -eq 1 ]]; then
    info "workers: self-provision only; deploy_target.sh did not SSH"
  fi
}

run_runtime() {
  local root release pull_flag count i dest arch self
  local -a ssh_dest=() ssh_arch=()
  local self_count=0
  root="$(jq_get '.runtime.root // "/work/goliath"')"
  release="$(jq_get '.runtime.release_dir // empty')"
  if [[ "$release" == "null" ]]; then
    release=""
  fi
  if [[ "$(jq_get '.runtime.pull_parabricks // false')" == "true" ]]; then
    pull_flag="--pull-parabricks"
  else
    pull_flag="--skip-docker-pull"
  fi
  if jq -e '.runtime.hosts | type == "array" and length > 0' "$PROFILE" >/dev/null; then
    count="$(jq_get '.runtime.hosts | length')"
    for ((i = 0; i < count; i++)); do
      ssh_dest+=("$(jq_get ".runtime.hosts[$i].ssh")")
      ssh_arch+=("$(jq -r --argjson i "$i" '.workers[$i].arch // "amd64"' "$PROFILE")")
    done
  else
    count="$(jq_get '.workers | length')"
    if [[ "$count" == "null" || -z "$count" ]]; then
      count=0
    fi
    for ((i = 0; i < count; i++)); do
      self="$(jq -r --argjson i "$i" '.workers[$i].self_provision // false' "$PROFILE")"
      if [[ "$self" == "true" ]]; then
        describe_self_provision_worker "$i" "runtime"
        self_count=$((self_count + 1))
        continue
      fi
      ssh_dest+=("$(jq_get ".workers[$i].ssh")")
      ssh_arch+=("$(jq_get ".workers[$i].arch")")
    done
  fi
  if [[ "${#ssh_dest[@]}" -eq 0 ]]; then
    if [[ "$self_count" -gt 0 ]]; then
      info "runtime: self-provision workers use /work/goliath/current or bootstrap the GitHub release on boot; skipping SSH promote"
      return 0
    fi
    die "no runtime hosts"
  fi
  [[ -n "$release" ]] || die "runtime.release_dir is required"
  for ((i = 0; i < ${#ssh_dest[@]}; i++)); do
    dest="${ssh_dest[$i]}"
    arch="${ssh_arch[$i]}"
    assert_remote_literal "runtime ssh" "$dest"
    assert_remote_literal "runtime arch" "$arch"
    assert_remote_literal "runtime root" "$root"
    assert_remote_literal "release dir" "$release"
    assert_remote_literal "rollouts environment" "$GOLIATH_ENV"
    info "runtime $dest arch=$arch release=$release"
    report_component start goliathomics-runtime "promote ${arch} ${dest}"
    install_remote_tree "$dest"
    if ssh_cmd "$dest" "sudo env GOLIATH_ENV='$GOLIATH_ENV' GOLIATH_ROLLOUTS_EXTERNAL=1 bash /tmp/goliath-deploy/scripts/promote_release.sh --root '$root' --release '$release' --arch '$arch' $pull_flag"; then
      report_component succeeded goliathomics-runtime "promoted ${arch} ${dest}"
    else
      report_component failed goliathomics-runtime "promote ${arch} ${dest} failed"
      return 1
    fi
  done
}

case "$COMPONENT" in
  schema) run_schema ;;
  gateway) run_gateway ;;
  workers) run_workers ;;
  runtime) run_runtime ;;
  all)
    run_schema
    run_gateway
    run_runtime
    run_workers
    ;;
esac

info "deploy_target ${TARGET} ${COMPONENT} finished"
