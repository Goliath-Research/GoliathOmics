#!/usr/bin/env bash
# Deploy one customer target described by deploy/targets/<name>.json.
# Secret values are read from the environment (GitHub environment secrets) or
# /etc/goliath/<target>.env. They are written to per-host env files over SSH
# and are never printed.

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
  info "workers: $(jq_get '[.workers[]? | "\(.name) \(.ssh) \(.arch) \(.capability // "")"] | join("; ")')"
  info "database host: $(jq_get '.database.host') kind=$(jq_get '.database.kind')"
  info "shared storage: $(jq_get '.shared_storage.provider // "none"') $(jq_get '.shared_storage.endpoint // ""')"
  info "long-term storage: $(jq_get '.long_term_storage.provider // "none"')"
  exit 0
fi

assert_remote_literal() {
  local label="$1"
  local value="$2"
  if [[ "$value" == *\'* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    die "${label} contains a quote or newline and cannot be sent to a remote shell"
  fi
}

ssh_cmd() {
  local dest="$1"
  shift
  local port identity
  port="$(jq -r --arg dest "$dest" '.. | objects | select(.ssh? == $dest) | .port // empty' "$PROFILE" | head -n 1)"
  identity="$(jq -r --arg dest "$dest" '.. | objects | select(.ssh? == $dest) | .identity_file // empty' "$PROFILE" | head -n 1)"
  local args=(-o BatchMode=yes -o ConnectTimeout=20)
  if [[ -n "$port" ]]; then
    args+=(-p "$port")
  fi
  if [[ -n "$identity" ]]; then
    args+=(-i "$identity")
  fi
  ssh "${args[@]}" "$dest" "$@"
}

scp_to() {
  local dest="$1"
  local src="$2"
  local remote="$3"
  # The remote redirect inherits the SSH user's umask (often 022). Create the
  # file mode 600 so gateway and worker secrets are not world-readable.
  ssh_cmd "$dest" "umask 077 && rm -f '$remote' && cat > '$remote' && chmod 600 '$remote'" <"$src"
}

# Upsert KEY=value lines from overlay onto base. Other keys in base are kept.
merge_env_overlay() {
  local base="$1"
  local overlay="$2"
  local out="$3"
  local line key next
  umask 077
  if [[ -f "$base" ]]; then
    cp "$base" "$out" || return 1
  else
    : >"$out" || return 1
  fi
  chmod 600 "$out" || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    next="$(mktemp)"
    grep -vE "^[[:space:]]*${key}=" "$out" >"$next" || true
    mv "$next" "$out" || return 1
    chmod 600 "$out" || return 1
    printf '%s\n' "$line" >>"$out" || return 1
  done <"$overlay"
}

# Print the remote env file. Status 42 means it is not there yet.
fetch_remote_env() {
  local dest="$1"
  local remote="$2"
  local out="$3"
  local status
  umask 077
  : >"$out"
  chmod 600 "$out"
  set +e
  ssh_cmd "$dest" "if [[ -f '$remote' ]]; then sudo cat '$remote'; else exit 42; fi" >"$out"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    chmod 600 "$out"
    return 0
  fi
  rm -f "$out"
  if [[ "$status" -eq 42 ]]; then
    return 42
  fi
  echo "[ERROR] failed to read ${dest}:${remote}" >&2
  return "$status"
}

# Merge overlay into one remote env file and remove the temp copy even if install fails.
install_merged_env() {
  local dest="$1"
  local remote_path="$2"
  local overlay="$3"
  local remote_tmp="$4"
  local existing="$stage_dir/existing.env"
  local merged="$stage_dir/merged.env"
  local status=0
  local remote_dir="${remote_path%/*}"
  rm -f "$existing" "$merged"
  fetch_remote_env "$dest" "$remote_path" "$existing" || status=$?
  if [[ "$status" -eq 42 ]]; then
    cp "$overlay" "$merged" || return 1
    chmod 600 "$merged" || return 1
  elif [[ "$status" -ne 0 ]]; then
    return "$status"
  else
    merge_env_overlay "$existing" "$overlay" "$merged" || return 1
  fi
  if ! scp_to "$dest" "$merged" "$remote_tmp"; then
    ssh_cmd "$dest" "rm -f '$remote_tmp'" || true
    return 1
  fi
  if ! ssh_cmd "$dest" "trap 'rm -f $remote_tmp' EXIT; sudo mkdir -p '$remote_dir' && sudo install -m 600 '$remote_tmp' '$remote_path'"; then
    ssh_cmd "$dest" "rm -f '$remote_tmp'" || true
    return 1
  fi
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
db_port="$(jq_get '.database.port // 5432')"
db_name="$(jq_get '.database.dbname')"
db_user="$(jq_get '.database.user')"
db_ssl="$(jq_get '.database.sslmode // "require"')"
db_password="$(resolve_ref "$(jq_get '.database.password // empty')")"

shared_provider="$(jq_get '.shared_storage.provider // "none"')"
shared_bucket="$(jq_get '.shared_storage.bucket // empty')"
shared_endpoint="$(jq_get '.shared_storage.endpoint // empty')"
shared_key="$(resolve_ref "$(jq_get '.shared_storage.access_key_id // empty')")"
shared_secret="$(resolve_ref "$(jq_get '.shared_storage.secret_access_key // empty')")"

long_provider="$(jq_get '.long_term_storage.provider // "none"')"
long_bucket="$(jq_get '.long_term_storage.bucket // empty')"
long_endpoint="$(jq_get '.long_term_storage.endpoint // empty')"
long_host="$(jq_get '.long_term_storage.host // empty')"
long_share="$(jq_get '.long_term_storage.share // empty')"
long_key="$(resolve_ref "$(jq_get '.long_term_storage.access_key_id // empty')")"
long_secret="$(resolve_ref "$(jq_get '.long_term_storage.secret_access_key // empty')")"

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
  apply="$(jq_get '.schema.apply // empty')"
  if [[ -z "$apply" || "$apply" == "null" || "$apply" == "none" ]]; then
    info "schema.apply=${apply:-unset}; skipping SQL"
    return 0
  fi
  # deploy_test_bed.sh is a PostgreSQL psql script. Pointing it at Azure SQL
  # fails the token guard or the client, and --component all stops there.
  if [[ "$db_kind" == "mssql" ]]; then
    info "database.kind=mssql; skipping PostgreSQL test-bed"
    return 0
  fi
  report_component start goliath-db-schema "schema ${TARGET}"
  export PGHOST="$db_host" PGPORT="$db_port" PGDATABASE="$db_name" PGUSER="$db_user"
  export PGSSLMODE="$db_ssl" PGPASSWORD="$db_password"
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
  [[ "$count" != "0" && "$count" != "null" ]] || die "gateway.hosts is empty"
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
        AZURE_SQL_PASSWORD "$db_password" \
        WF_GATEWAY_HOST "$bind" \
        WF_GATEWAY_PORT "$port" \
        GOLIATH_ENV "$GOLIATH_ENV"
    else
      write_kv_file "$envfile" \
        BACKEND_DB postgres \
        POSTGRES_HOST "$db_host" \
        POSTGRES_PORT "$db_port" \
        POSTGRES_DB "$db_name" \
        POSTGRES_USER "$db_user" \
        POSTGRES_PASSWORD "$db_password" \
        POSTGRES_SSLMODE "$db_ssl" \
        WF_GATEWAY_HOST "$bind" \
        WF_GATEWAY_PORT "$port" \
        GOLIATH_ENV "$GOLIATH_ENV"
    fi
    install_remote_tree "$dest"
    scp_to "$dest" "$envfile" /tmp/goliath-gateway.env
    rm -f "$envfile"
    # methyl-gateway.service reads <root>/env/gateway.env. The unit has no
    # --env-file or User= flag. An extra profile env_file is copied beside it.
    # trap removes the secret copy when mkdir/install fails before the explicit rm.
    local gw_env_cmd="trap 'rm -f /tmp/goliath-gateway.env' EXIT; sudo mkdir -p '$root/env' && sudo install -m 600 -o root /tmp/goliath-gateway.env '$root/env/gateway.env'"
    if [[ -n "$remote_env" && "$remote_env" != "null" && "$remote_env" != "$root/env/gateway.env" ]]; then
      gw_env_cmd+=" && sudo install -m 600 -o root /tmp/goliath-gateway.env '$remote_env'"
    fi
    gw_env_cmd+=" && rm -f /tmp/goliath-gateway.env && sudo env GOLIATH_ENV='$GOLIATH_ENV' GOLIATH_ROLLOUTS_EXTERNAL=1 bash /tmp/goliath-deploy/scripts/install_gateway_systemd.sh --root '$root'"
    if ssh_cmd "$dest" "$gw_env_cmd"; then
      report_component succeeded goliath-gateway "gateway ${dest}"
      report_component succeeded goliath-reclaim-leases "reclaim-leases ${dest}"
    else
      ssh_cmd "$dest" "rm -f /tmp/goliath-gateway.env" || true
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

run_workers() {
  local count
  count="$(jq_get '.workers | length')"
  [[ "$count" != "0" && "$count" != "null" ]] || die "workers is empty"
  local i dest arch cap root host msg
  for ((i = 0; i < count; i++)); do
    dest="$(jq_get ".workers[$i].ssh")"
    arch="$(jq_get ".workers[$i].arch")"
    cap="$(jq_get ".workers[$i].capability // empty")"
    root="$(jq -r --argjson i "$i" '.workers[$i].install_root // "/work/goliath"' "$PROFILE")"
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
    # methyl-worker.service loads /work/goliath/env/worker.env. Merge API and
    # storage keys into that file (and <root>/env/worker.env when it differs)
    # so promote's extractor, profile, and TMPDIR settings stay in place.
    local cap_flag=""
    if [[ -n "$cap" && "$cap" != "null" ]]; then
      cap_flag="--capability '$cap'"
    fi
    local shared_env="/work/goliath/env/worker.env"
    local root_env="$root/env/worker.env"
    local worker_ok=0
    if install_merged_env "$dest" "$root_env" "$envfile" /tmp/goliath-worker.env \
      && { [[ "$root_env" == "$shared_env" ]] || install_merged_env "$dest" "$shared_env" "$envfile" /tmp/goliath-worker.env; } \
      && ssh_cmd "$dest" "sudo env GOLIATH_ENV='$GOLIATH_ENV' GOLIATH_ROLLOUTS_EXTERNAL=1 bash /tmp/goliath-deploy/scripts/install_worker_systemd.sh --root '$root' --arch '$arch' $cap_flag"; then
      worker_ok=1
    fi
    rm -f "$envfile"
    if [[ "$worker_ok" -eq 1 ]]; then
      report_component succeeded goliath-worker "$msg"
    else
      report_component failed goliath-worker "$msg failed"
      return 1
    fi
  done
}

stage_release_dir() {
  local dest="$1"
  local remote_dir="$2"
  local src="${GOLIATH_RELEASE_SRC:-}"
  local bundle="$stage_dir/release-bundle.tgz"
  [[ -n "$src" ]] || return 0
  [[ -d "$src" ]] || die "GOLIATH_RELEASE_SRC is not a directory: $src"
  [[ -f "$src/manifest.json" ]] || die "GOLIATH_RELEASE_SRC has no manifest.json: $src"
  info "staging release $src -> ${dest}:${remote_dir}"
  tar -C "$src" -czf "$bundle" . || return 1
  if ! scp_to "$dest" "$bundle" /tmp/goliath-release.tgz; then
    ssh_cmd "$dest" "rm -f /tmp/goliath-release.tgz" || true
    return 1
  fi
  # Replace the tree so a previous release at this path cannot be promoted.
  if ! ssh_cmd "$dest" "trap 'rm -f /tmp/goliath-release.tgz' EXIT; sudo mkdir -p '$remote_dir' && sudo find '$remote_dir' -mindepth 1 -maxdepth 1 -exec rm -rf {} + && sudo tar -xzf /tmp/goliath-release.tgz -C '$remote_dir'"; then
    ssh_cmd "$dest" "rm -f /tmp/goliath-release.tgz" || true
    return 1
  fi
}

run_runtime() {
  local root release pull_flag count i dest arch arch_flag arch_label use_runtime_hosts
  root="$(jq_get '.runtime.root // "/work/goliath"')"
  release="$(jq_get '.runtime.release_dir // empty')"
  [[ -n "$release" && "$release" != "null" ]] || die "runtime.release_dir is required"
  if [[ -n "${GOLIATH_RELEASE_SRC:-}" ]]; then
    [[ -d "$GOLIATH_RELEASE_SRC" ]] || die "GOLIATH_RELEASE_SRC is not a directory: $GOLIATH_RELEASE_SRC"
    [[ -f "$GOLIATH_RELEASE_SRC/manifest.json" ]] || die "GOLIATH_RELEASE_SRC has no manifest.json: $GOLIATH_RELEASE_SRC"
  fi
  if [[ "$(jq_get '.runtime.pull_parabricks // false')" == "true" ]]; then
    pull_flag="--pull-parabricks"
  else
    pull_flag="--skip-docker-pull"
  fi
  if jq -e '.runtime.hosts | type == "array" and length > 0' "$PROFILE" >/dev/null; then
    use_runtime_hosts=1
  else
    use_runtime_hosts=0
  fi
  count="$(jq_get '(.runtime.hosts // .workers) | length')"
  [[ "$count" != "0" && "$count" != "null" ]] || die "no runtime hosts"
  for ((i = 0; i < count; i++)); do
    arch_flag=""
    if [[ "$use_runtime_hosts" -eq 1 ]]; then
      dest="$(jq_get ".runtime.hosts[$i].ssh")"
      # Arch belongs to this host. Do not borrow workers[$i] — the lists can
      # differ in length and order. Detect on the host when it is unset.
      arch="$(jq_get ".runtime.hosts[$i].arch // empty")"
      if [[ -z "$arch" || "$arch" == "null" ]]; then
        arch="$(jq -r --arg ssh "$dest" '[.workers[]? | select(.ssh == $ssh) | .arch] | first // empty' "$PROFILE")"
      fi
    else
      dest="$(jq_get ".workers[$i].ssh")"
      arch="$(jq_get ".workers[$i].arch")"
    fi
    if [[ -z "$arch" || "$arch" == "null" ]]; then
      arch=""
    fi
    assert_remote_literal "runtime ssh" "$dest"
    assert_remote_literal "runtime arch" "$arch"
    assert_remote_literal "runtime root" "$root"
    assert_remote_literal "release dir" "$release"
    assert_remote_literal "rollouts environment" "$GOLIATH_ENV"
    if [[ -n "$arch" ]]; then
      arch_flag="--arch '$arch'"
      arch_label="$arch"
    else
      arch_label="detect"
    fi
    info "runtime $dest arch=$arch_label release=$release"
    report_component start goliathomics-runtime "promote ${arch_label} ${dest}"
    install_remote_tree "$dest"
    if ! stage_release_dir "$dest" "$release"; then
      report_component failed goliathomics-runtime "stage ${arch_label} ${dest} failed"
      return 1
    fi
    if ssh_cmd "$dest" "sudo env GOLIATH_ENV='$GOLIATH_ENV' GOLIATH_ROLLOUTS_EXTERNAL=1 bash /tmp/goliath-deploy/scripts/promote_release.sh --root '$root' --release '$release' $arch_flag $pull_flag"; then
      report_component succeeded goliathomics-runtime "promoted ${arch_label} ${dest}"
    else
      report_component failed goliathomics-runtime "promote ${arch_label} ${dest} failed"
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
