#!/usr/bin/env bash
# Deploy one customer target described by deploy/targets/<name>.json.
# Secret values are read from the environment (GitHub environment secrets) or
# /etc/goliath/<target>.env. They are written to per-host env files over SSH
# and are never printed.
#
# Workers with self_provision=true (a RunPod pool) are not reached over SSH.
# deploy_target.sh prints how those pods boot themselves. The template start
# command is the one line in deploy/runpod/template-start-command.sh. It
# downloads deploy/runpod/goliath-worker-bootstrap.sh from the goliathomics
# release, and that script installs deploy/runpod/goliath-worker-start.sh
# onto an empty /work. See deploy/runpod/README.md.
#
# The gateway component also installs nginx and TLS
# (scripts/setup_gateway_nginx.sh). The default certificate is self-signed
# for the public IP and is created once on the host. gateway.tls.mode
# letsencrypt is optional and needs a domain.
#
# SSH and scp read host keys from ~/.ssh/known_hosts. The release-deploy
# workflow writes the GOLIATH_GATEWAY_KNOWN_HOSTS secret there before it
# calls this script. This script does not run ssh-keyscan and does not
# set StrictHostKeyChecking=no.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/deploy_target.sh --target NAME [--component gateway|workers|runtime|schema|all] [--force-schema] [--dry-run]

Options:
  --target NAME       Profile deploy/targets/<name>.json
                      (example profiles are allowed only with --dry-run)
  --component NAME    gateway, workers, runtime, schema, or all (default: all)
  --force-schema      Apply the release's GoliathApp SQL even if
                      wf.schema_release already records this release
                      (also GOLIATH_SCHEMA_FORCE=1)
  --dry-run           Print hosts and secret *names*. Do not SSH or print values.
  -h, --help

GOLIATH_ENV is set from the profile rollouts_environment before any install
script runs, so report_deploy.sh files the events on that environment.

Workers marked self_provision do not use SSH. --component workers and
--component all print the pod template and return without opening a session.
Schema apply uses database.schema_host when it is set (Neon direct host),
and database.host otherwise (the pooled host the gateway keeps using).

GoliathApp schema: the release (GOLIATH_RELEASE_SRC) carries GoliathApp's
sql_pg scripts in goliathapp-sql/. --component schema, runtime and all apply
them from this runner (never from pods or the gateway) once per release:
wf.schema_release records the release and later deploys skip it. Profile
field schema.goliathapp: per-release (default) or none.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

TARGET=""
COMPONENT="all"
DRY_RUN=0
FORCE_SCHEMA="${GOLIATH_SCHEMA_FORCE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --component) COMPONENT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force-schema) FORCE_SCHEMA=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
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

gateway_public_host() {
  local host="$1"
  host="${host#*://}"
  host="${host%%/*}"
  host="${host%%\?*}"
  host="${host##*@}"
  if [[ "$host" == *:* ]]; then
    host="${host%%:*}"
  fi
  printf '%s' "$host"
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
  info "gateway public_base_url=$(jq_get '.gateway.public_base_url // ""') tls=$(jq_get '.gateway.tls.mode // "self-signed"')"
  info "workers: $(jq_get '[.workers[]? | if .self_provision == true then "\(.name) self_provision \(.provider // "unknown") \(.arch) \(.template // "")" else "\(.name) \(.ssh // "") \(.arch) \(.capability // "")" end] | join("; ")')"
  info "database host: $(jq_get '.database.host') schema_host=$(jq_get '.database.schema_host // ""') kind=$(jq_get '.database.kind') channel_binding=$(jq_get '.database.channel_binding // ""')"
  info "schema: goliathapp=$(jq_get '.schema.goliathapp // "per-release"') apply=$(jq_get '.schema.apply // "unset"') force=${FORCE_SCHEMA}"
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
  local port identity raw_identity known_hosts
  port="$(jq -r --arg dest "$dest" '.. | objects | select(.ssh? == $dest) | .port // empty' "$PROFILE" | head -n 1)"
  raw_identity="$(jq -r --arg dest "$dest" '.. | objects | select(.ssh? == $dest) | .identity_file // empty' "$PROFILE" | head -n 1)"
  identity="$(materialize_identity "$raw_identity")" || return 1
  # Pin the user known_hosts file. scp_to runs through this function, so
  # both SSH and file copy consult the same path the workflow populates.
  known_hosts="${HOME}/.ssh/known_hosts"
  local args=(
    -o BatchMode=yes
    -o ConnectTimeout=20
    -o "UserKnownHostsFile=${known_hosts}"
  )
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
# The probe is test(1) under sudo, not [[. A dash login shell treats [[ as a
# missing command, the if fails, and exit 42 would replace a real worker.env
# with only the API overlay.
fetch_remote_env() {
  local dest="$1"
  local remote="$2"
  local out="$3"
  local status probe
  umask 077
  : >"$out"
  chmod 600 "$out"
  probe="if sudo test -f '$remote'; then sudo cat '$remote'; else status=\$?; if [ \"\$status\" -eq 1 ]; then exit 42; fi; exit \"\$status\"; fi"
  set +e
  ssh_cmd "$dest" "$probe" >"$out"
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

export_schema_pg_env() {
  # schema_host is the non-pooled Neon endpoint. The gateway keeps using host.
  # PGDATABASE is a libpq parameter, so the mixed-case name GoliathOmics is
  # passed as-is. Inside SQL that identifier must be double-quoted: "GoliathOmics".
  export PGHOST="$db_schema_host" PGPORT="$db_port" PGDATABASE="$db_name" PGUSER="$db_user"
  export PGSSLMODE="$db_ssl" PGPASSWORD="$DB_PASSWORD"
  if [[ -n "$db_channel" ]]; then
    export PGCHANNELBINDING="$db_channel"
  fi
}

# GoliathApp's workflow_engine/sql_pg scripts travel inside the release
# (goliathapp-sql/, bundled by build_release.sh from the same GoliathApp
# commit as the methyl-gateway wheel). They are applied from this runner,
# which holds the database secret; pods and the gateway never run SQL.
# apply_goliathapp_schema.sh records each applied release in
# wf.schema_release, so only the first deploy of a release upgrades the
# database. Later deploys of the same release skip it unless --force-schema.
GOLIATHAPP_SCHEMA_DONE=0

# Status 0: apply (or check) now. Status 1: nothing to do; the reason is printed.
goliathapp_schema_wanted() {
  local mode
  [[ "$GOLIATHAPP_SCHEMA_DONE" -eq 0 ]] || return 1
  mode="$(jq_get '.schema.goliathapp // "per-release"')"
  case "$mode" in
    per-release) ;;
    none)
      info "schema.goliathapp=none; skipping GoliathApp SQL"
      return 1
      ;;
    *) die "schema.goliathapp must be per-release or none" ;;
  esac
  if [[ -z "${GOLIATH_RELEASE_SRC:-}" ]]; then
    info "GOLIATH_RELEASE_SRC is not set; skipping GoliathApp SQL (release-deploy sets it to the release bundle)"
    return 1
  fi
  [[ -d "$GOLIATH_RELEASE_SRC" ]] || die "GOLIATH_RELEASE_SRC is not a directory: $GOLIATH_RELEASE_SRC"
  [[ -f "$GOLIATH_RELEASE_SRC/manifest.json" ]] || die "GOLIATH_RELEASE_SRC has no manifest.json: $GOLIATH_RELEASE_SRC"
  return 0
}

apply_goliathapp_schema() {
  local -a args=(--release-dir "$GOLIATH_RELEASE_SRC")
  GOLIATHAPP_SCHEMA_DONE=1
  if [[ "$FORCE_SCHEMA" == "1" ]]; then
    args+=(--force)
  fi
  info "GoliathApp schema host=${db_schema_host} db=${db_name} release=${GOLIATH_RELEASE_SRC}"
  bash "$REPO_ROOT/scripts/apply_goliathapp_schema.sh" "${args[@]}"
}

run_schema() {
  local apply want_app=0 want_bed=1
  apply="$(jq_get '.schema.apply // empty')"
  if goliathapp_schema_wanted; then
    want_app=1
  fi
  if [[ -z "$apply" || "$apply" == "null" || "$apply" == "none" ]]; then
    info "schema.apply=${apply:-unset}; skipping test-bed SQL"
    want_bed=0
  fi
  if [[ "$want_app" -eq 0 && "$want_bed" -eq 0 ]]; then
    return 0
  fi
  load_db_password
  report_component start goliath-db-schema "schema ${TARGET}"
  info "schema apply host=${db_schema_host} (gateway host=${db_host})"
  export_schema_pg_env
  export GOLIATH_DEPLOY_TARGET="$TARGET"
  # Engine DDL first: the test-bed seeds use GoliathApp tables and functions.
  if [[ "$want_app" -eq 1 ]] && ! apply_goliathapp_schema; then
    report_component failed goliath-db-schema "schema ${TARGET} failed (GoliathApp SQL)"
    return 1
  fi
  if [[ "$want_bed" -eq 1 ]] && ! GOLIATH_ROLLOUTS_EXTERNAL=1 bash "$REPO_ROOT/workflow_engine/sql_pg/deploy_test_bed.sh"; then
    report_component failed goliath-db-schema "schema ${TARGET} failed"
    return 1
  fi
  report_component succeeded goliath-db-schema "schema ${TARGET}"
  # DomainPrograms + cfg.analyte after schema/test-bed (once per release).
  run_catalog_sync || return 1
}

# Once-per-release SamplePrep DomainProgram + cfg.analyte / process-pack row sync.
# Gated by wf.catalog_sync_release (same release id as wf.schema_release). The
# GitHub runner has no /work: deploy_workflow_definitions.sh writes
# workflow_versions.json under RUNNER_TEMP/goliath-env (uploaded as an artifact).
# Workers resolve workflow_version_id from the DB (program_path /
# get_workflow_definition_by_name); the JSON is optional for canary/smoke.
run_catalog_sync() {
  local release_id already
  if [[ -z "${GOLIATH_RELEASE_SRC:-}" || ! -f "${GOLIATH_RELEASE_SRC}/manifest.json" ]]; then
    info "catalog sync: GOLIATH_RELEASE_SRC unset; skipping DomainProgram / analyte sync"
    return 0
  fi
  release_id="$(jq -r '.version // empty' "${GOLIATH_RELEASE_SRC}/manifest.json")"
  [[ -n "$release_id" ]] || die "catalog sync: release manifest has no version"
  load_db_password
  export_schema_pg_env
  already="$(psql -qAt -v ON_ERROR_STOP=1 -c \
    "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='wf' AND table_name='catalog_sync_release');")"
  if [[ "$already" == "t" ]]; then
    already="$(psql -qAt -v ON_ERROR_STOP=1 -c \
      "SELECT EXISTS (SELECT 1 FROM wf.catalog_sync_release WHERE release='${release_id}');")"
    if [[ "$already" == "t" && "${FORCE_SCHEMA:-0}" != "1" && "${GOLIATH_FORCE_CATALOG_SYNC:-0}" != "1" ]]; then
      info "catalog sync: release ${release_id} already recorded; skip (set GOLIATH_FORCE_CATALOG_SYNC=1 or --force-schema)"
      return 0
    fi
  fi
  # Catalog sync needs GoliathApp cfg.* (cfg_schema + registry). Synthetic
  # test releases that only ship a ledger stub have no cfg schema — skip.
  if ! psql -qAt -v ON_ERROR_STOP=1 -c \
    "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name='cfg');" \
    | grep -qx t; then
    info "catalog sync: cfg schema absent (incomplete GoliathApp SQL in this release); skip"
    return 0
  fi
  report_component start goliath-db-catalog "catalog sync ${TARGET} ${release_id}"
  info "catalog sync: analyte DDL + DomainPrograms + cfg rows for ${release_id}"
  # Ensure Omics analyte DDL (and optional GoliathApp process-pack re-apply)
  if ! bash "$REPO_ROOT/scripts/deploy_process_pack_catalog.sh" --backend postgres; then
    report_component failed goliath-db-catalog "deploy_process_pack_catalog DDL failed"
    return 1
  fi
  # DomainPrograms (SamplePrepPipeline, …). Keep instances on replace.
  export GOLIATH_ENV_DIR="${GOLIATH_ENV_DIR:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/goliath-env}"
  export GOLIATH_DEPLOY_KEEP_INSTANCES="${GOLIATH_DEPLOY_KEEP_INSTANCES:-1}"
  export BACKEND_DB=postgres
  export POSTGRES_HOST="${PGHOST}"
  export POSTGRES_PORT="${PGPORT:-5432}"
  export POSTGRES_DB="${PGDATABASE}"
  export POSTGRES_USER="${PGUSER}"
  export POSTGRES_PASSWORD="${PGPASSWORD}"
  # DomainProgram compile needs methyldomain + workers + contract; rest comes from the gateway wheel.
  local catalog_py_path="${REPO_ROOT}/workflow_engine:${REPO_ROOT}/workflow_engine/domain:${REPO_ROOT}/workflow_engine/contract:${REPO_ROOT}/packages/methyldomain:${REPO_ROOT}/workers"
  if [[ -z "${PYTHONPATH:-}" || "${PYTHONPATH}" != *workflow_engine* ]]; then
    export PYTHONPATH="${catalog_py_path}${PYTHONPATH:+:$PYTHONPATH}"
  else
    export PYTHONPATH="${catalog_py_path}:${PYTHONPATH}"
  fi
  local wheel_meta wheel=""
  if wheel_meta="$(bash "$REPO_ROOT/scripts/install_gateway_venv.sh" --find-wheel "$GOLIATH_RELEASE_SRC" 2>/dev/null)"; then
    # install_gateway_venv --find-wheel prints WHEEL=... (not path=)
    while IFS= read -r line || [[ -n "$line" ]]; do
      case "$line" in
        WHEEL=*) wheel="${line#WHEEL=}" ;;
        path=*) wheel="${line#path=}" ;;
      esac
    done <<< "$wheel_meta"
  fi
  local py=python3
  if [[ -n "$wheel" && -f "$wheel" ]]; then
    local venv="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/goliath-catalog-venv"
    if [[ ! -x "$venv/bin/python" ]]; then
      python3 -m venv "$venv"
      "$venv/bin/pip" -q install --upgrade pip
      "$venv/bin/pip" -q install "$wheel" "psycopg[binary]"
    fi
    py="$venv/bin/python"
    local sp
    sp="$(find "$venv/lib" -maxdepth 2 -type d -name site-packages | head -n1)"
    export PYTHONPATH="${catalog_py_path}${sp:+:$sp}${PYTHONPATH:+:$PYTHONPATH}"
  else
    warn "catalog sync: no methyl-gateway wheel under GOLIATH_RELEASE_SRC; deploy_workflow_definitions may fail without rest"
    python3 -m pip -q install "psycopg[binary]" >/dev/null 2>&1 || true
  fi
  # DomainProgram create resolves action_name against wf.workflow_action.
  # Seed from schemas/actions/catalog.json before deploy_workflow_definitions
  # (sync_cfg --skip-seed only upserts cfg.* rows).
  info "catalog sync: seeding wf.workflow_action from schemas/actions/catalog.json"
  if ! (
    cd "$REPO_ROOT"
    PYTHONPATH="${catalog_py_path}${PYTHONPATH:+:$PYTHONPATH}"       "$py" "$REPO_ROOT/workflow_engine/sql_pg/seed_action_catalog.py" --use-db
  ); then
    report_component failed goliath-db-catalog "seed_action_catalog failed"
    return 1
  fi
  if ! PYTHON_BIN="$py" bash "$REPO_ROOT/scripts/deploy_workflow_definitions.sh" --keep-instances; then
    report_component failed goliath-db-catalog "deploy_workflow_definitions failed"
    return 1
  fi
  if ! (
    cd "$REPO_ROOT"
    PYTHONPATH="${catalog_py_path}${PYTHONPATH:+:$PYTHONPATH}" \
      "$py" "$REPO_ROOT/scripts/sync_cfg_profiles_and_action_catalog.py" \
        --backend postgres --skip-seed
  ); then
    report_component failed goliath-db-catalog "sync_cfg_profiles_and_action_catalog failed"
    return 1
  fi
  psql -q -v ON_ERROR_STOP=1 <<SQL
CREATE TABLE IF NOT EXISTS wf.catalog_sync_release (
  release text PRIMARY KEY,
  applied_at_utc timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  applied_by text NOT NULL DEFAULT current_user,
  apply_count integer NOT NULL DEFAULT 1
);
COMMENT ON TABLE wf.catalog_sync_release IS
  'GoliathOmics releases whose SamplePrep DomainPrograms + cfg.analyte sync ran (scripts/deploy_target.sh).';
INSERT INTO wf.catalog_sync_release (release)
VALUES ('${release_id}')
ON CONFLICT (release) DO UPDATE SET
  applied_at_utc = EXCLUDED.applied_at_utc,
  applied_by = EXCLUDED.applied_by,
  apply_count = wf.catalog_sync_release.apply_count + 1;
SQL
  info "catalog sync: recorded release ${release_id}; versions at ${GOLIATH_ENV_DIR}/workflow_versions.json"
  report_component succeeded goliath-db-catalog "catalog sync ${TARGET} ${release_id}"
}

# --component runtime alone also stages a release. Upgrade the database first
# (a no-op when the release is already recorded, or when all ran schema).
run_runtime_schema() {
  goliathapp_schema_wanted || return 0
  load_db_password
  report_component start goliath-db-schema "schema ${TARGET} (runtime)"
  export_schema_pg_env
  if apply_goliathapp_schema; then
    report_component succeeded goliath-db-schema "schema ${TARGET} (runtime)"
  else
    report_component failed goliath-db-schema "schema ${TARGET} failed (GoliathApp SQL)"
    return 1
  fi
}

# Scripts each host role needs in /tmp/goliath-deploy. The gateway is only the
# REST API (workers -> DB): it gets the gateway installers, its systemd units
# and the nginx site, never worker/runtime scripts.
install_remote_tree() {
  local dest="$1" role="${2:-node}"
  local bundle="$stage_dir/bundle.tgz"
  local -a files
  if [[ "$role" == "gateway" ]]; then
    files=(
      scripts/report_deploy.sh
      scripts/rollouts_hook.sh
      scripts/detect_platform.sh
      scripts/platform_matrix.env
      scripts/install_gateway_systemd.sh
      scripts/install_gateway_venv.sh
      scripts/install_reclaim_leases_timer.sh
      scripts/setup_gateway_nginx.sh
      deploy/systemd/methyl-gateway.service
      deploy/systemd/methyl-reclaim-leases.service
      deploy/systemd/methyl-reclaim-leases.timer
      deploy/nginx
    )
  else
    files=(
      scripts/report_deploy.sh
      scripts/rollouts_hook.sh
      scripts/install_worker_systemd.sh
      scripts/promote_release.sh
      scripts/install_release.sh
      scripts/worker_venv_local.sh
      scripts/write_worker_env.sh
      scripts/setup_gpu_node.sh
      scripts/verify_setup.sh
      scripts/verify_e2e_node.sh
      scripts/bootstrap_goliath.sh
      scripts/detect_platform.sh
      scripts/platform_matrix.env
      scripts/install_methyl_extractor_tarball.sh
      deploy/systemd
    )
  fi
  tar -C "$REPO_ROOT" -czf "$bundle" "${files[@]}"
  scp_to "$dest" "$bundle" /tmp/goliath-deploy-bundle.tgz
  ssh_cmd "$dest" "rm -rf /tmp/goliath-deploy && mkdir -p /tmp/goliath-deploy && tar --no-same-owner --no-same-permissions -m -xzf /tmp/goliath-deploy-bundle.tgz -C /tmp/goliath-deploy && rm -f /tmp/goliath-deploy-bundle.tgz"
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
  # The release artifact is already on the runner (GOLIATH_RELEASE_SRC).
  # Runtime promote does not stage it onto this gateway: goliath-test workers
  # are self-provision and skip SSH. Without the wheel, /opt/methyl-gateway
  # only receives env/ and methyl-gateway.service exits 203/EXEC.
  if [[ -z "${GOLIATH_RELEASE_SRC:-}" ]]; then
    die "GOLIATH_RELEASE_SRC is not set. Gateway deploy installs the methyl-gateway package from the goliathomics release into the gateway venv before enabling the service."
  fi
  [[ -d "$GOLIATH_RELEASE_SRC" ]] || die "GOLIATH_RELEASE_SRC is not a directory: $GOLIATH_RELEASE_SRC"
  [[ -f "$GOLIATH_RELEASE_SRC/manifest.json" ]] || die "GOLIATH_RELEASE_SRC has no manifest.json: $GOLIATH_RELEASE_SRC"
  # The core package wheel is identified by the methyl-gateway console script
  # in entry_points.txt. Plugin wheels in the same directory do not match,
  # and a filename such as methyl-pipeline-*.whl is not enough.
  local gateway_wheel_meta="" gateway_wheel_path=""
  if ! gateway_wheel_meta="$(bash "$REPO_ROOT/scripts/install_gateway_venv.sh" --find-wheel "$GOLIATH_RELEASE_SRC")"; then
    die "no wheel under $GOLIATH_RELEASE_SRC provides the methyl-gateway console script in entry_points.txt"
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      WHEEL=*) gateway_wheel_path="${line#WHEEL=}" ;;
    esac
  done <<<"$gateway_wheel_meta"
  [[ -n "$gateway_wheel_path" && -f "$gateway_wheel_path" ]] || die "gateway wheel discovery did not return a file under $GOLIATH_RELEASE_SRC"
  info "gateway console script wheel: $gateway_wheel_path"
  local tls_mode tls_domain tls_email tls_san
  tls_mode="$(jq_get '.gateway.tls.mode // "self-signed"')"
  if [[ -z "$tls_mode" || "$tls_mode" == "null" ]]; then
    tls_mode="self-signed"
  fi
  case "$tls_mode" in
    self-signed|letsencrypt) ;;
    *) die "gateway.tls.mode must be self-signed or letsencrypt" ;;
  esac
  tls_domain="$(jq_get '.gateway.tls.domain // empty')"
  tls_email="$(jq_get '.gateway.tls.email // empty')"
  tls_san="$(jq_get '.gateway.tls.san_ip // empty')"
  if [[ "$tls_domain" == "null" ]]; then
    tls_domain=""
  fi
  if [[ "$tls_email" == "null" ]]; then
    tls_email=""
  fi
  if [[ "$tls_san" == "null" ]]; then
    tls_san=""
  fi
  if [[ "$tls_mode" == "letsencrypt" ]]; then
    [[ -n "$tls_domain" ]] || die "gateway.tls.mode=letsencrypt requires gateway.tls.domain"
    [[ -n "$tls_email" ]] || die "gateway.tls.mode=letsencrypt requires gateway.tls.email"
  fi
  local i dest envfile remote_env root user port bind release_remote public_host
  root="$(jq_get '.gateway.install_root // "/opt/methyl-gateway"')"
  user="$(jq_get '.gateway.user // "goliath"')"
  port="$(jq_get '.gateway.port // 8080')"
  bind="$(jq_get '.gateway.bind_host // "127.0.0.1"')"
  remote_env="$(jq_get '.gateway.env_file // empty')"
  release_remote="$root/gateway-dist"
  for ((i = 0; i < count; i++)); do
    dest="$(jq_get ".gateway.hosts[$i].ssh")"
    assert_remote_literal "gateway ssh" "$dest"
    assert_remote_literal "gateway root" "$root"
    assert_remote_literal "gateway user" "$user"
    assert_remote_literal "gateway env file" "$remote_env"
    assert_remote_literal "gateway release dir" "$release_remote"
    assert_remote_literal "rollouts environment" "$GOLIATH_ENV"
    info "gateway host $dest"
    report_component start goliath-gateway "gateway ${dest}"
    # install_gateway_systemd.sh also installs the reclaim timer. EXTERNAL skips
    # the nested reports, so the driver records both services for this host.
    report_component start goliath-reclaim-leases "reclaim-leases ${dest}"
    envfile="$stage_dir/gateway.env"
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
    install_remote_tree "$dest" gateway
    if ! stage_gateway_release "$dest" "$root"; then
      report_component failed goliath-gateway "gateway ${dest} failed"
      report_component failed goliath-reclaim-leases "reclaim-leases ${dest} failed"
      return 1
    fi
    scp_to "$dest" "$envfile" /tmp/goliath-gateway.env
    rm -f "$envfile"
    # methyl-gateway.service reads <root>/env/gateway.env. The unit has no
    # --env-file or User= flag. An extra profile env_file is copied beside it.
    # trap removes the secret copy when mkdir/install fails before the explicit rm.
    local gw_env_cmd="trap 'rm -f /tmp/goliath-gateway.env' EXIT; sudo mkdir -p '$root/env' && sudo install -m 600 -o root /tmp/goliath-gateway.env '$root/env/gateway.env'"
    if [[ -n "$remote_env" && "$remote_env" != "null" && "$remote_env" != "$root/env/gateway.env" ]]; then
      gw_env_cmd+=" && sudo install -m 600 -o root /tmp/goliath-gateway.env '$remote_env'"
    fi
    # install_gateway_systemd.sh creates venv-<arch> and installs the wheel
    # before systemctl enable/restart. The reclaim timer uses that venv too.
    # nginx then publishes https://<public host>/v1. The certificate step is
    # idempotent: a self-signed key already on the host is kept.
    public_host="$(gateway_public_host "$gateway_url")"
    if [[ -z "$public_host" || "$public_host" == "null" ]]; then
      public_host="${dest##*@}"
    fi
    assert_remote_literal "gateway public host" "$public_host"
    assert_remote_literal "gateway tls mode" "$tls_mode"
    assert_remote_literal "gateway bind" "$bind"
    assert_remote_literal "gateway port" "$port"
    info "gateway tls mode=${tls_mode} public_host=${public_host}"
    gw_env_cmd+=" && rm -f /tmp/goliath-gateway.env && sudo env GOLIATH_ENV='$GOLIATH_ENV' GOLIATH_ROLLOUTS_EXTERNAL=1 bash /tmp/goliath-deploy/scripts/install_gateway_systemd.sh --root '$root' --release '$release_remote'"
    gw_env_cmd+=" && sudo bash /tmp/goliath-deploy/scripts/setup_gateway_nginx.sh --public-host '$public_host' --tls '$tls_mode' --bind '$bind' --port '$port'"
    if [[ -n "$tls_san" ]]; then
      assert_remote_literal "gateway tls san" "$tls_san"
      gw_env_cmd+=" --san-ip '$tls_san'"
    fi
    if [[ "$tls_mode" == "letsencrypt" ]]; then
      assert_remote_literal "gateway tls domain" "$tls_domain"
      assert_remote_literal "gateway tls email" "$tls_email"
      gw_env_cmd+=" --domain '$tls_domain' --email '$tls_email'"
    fi
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

describe_self_provision_worker() {
  local i="$1"
  local purpose="$2"
  local name provider arch template image mount gpu vcpu ram disk root
  name="$(jq -r --argjson i "$i" '.workers[$i].name' "$PROFILE")"
  provider="$(jq -r --argjson i "$i" '.workers[$i].provider // "unknown"' "$PROFILE")"
  arch="$(jq -r --argjson i "$i" '.workers[$i].arch' "$PROFILE")"
  template="$(jq -r --argjson i "$i" '.workers[$i].template // ""' "$PROFILE")"
  image="$(jq -r --argjson i "$i" '.workers[$i].image // ""' "$PROFILE")"
  mount="$(jq -r --argjson i "$i" '.workers[$i].volume_mount // "/work"' "$PROFILE")"
  vcpu="$(jq -r --argjson i "$i" '.workers[$i].vcpu // ""' "$PROFILE")"
  ram="$(jq -r --argjson i "$i" '.workers[$i].memory_gb // ""' "$PROFILE")"
  disk="$(jq -r --argjson i "$i" 'if (.workers[$i] | has("local_disk_gb")) then .workers[$i].local_disk_gb else 200 end' "$PROFILE")"
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
  [[ "$disk" =~ ^[0-9]+$ && "$disk" -ge 150 ]] || die "worker ${name} local_disk_gb must be an integer >= 150 (got '${disk}')"
  info "  gpu=${gpu} vcpu=${vcpu} memory_gb=${ram} local_disk_gb=${disk} volume=${mount} install_root=${root}"
  info "  boot: template start command fetches deploy/runpod/goliath-worker-bootstrap.sh, which installs deploy/runpod/goliath-worker-start.sh and scripts/start_shared_worker.sh onto an empty /work"
  info "  the node enrolls with WORKER_API_BASE and reports goliath-worker (node id, host, arch, GPU). The RunPod adapter sets the node id from RUNPOD_POD_ID."
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
  if [[ "$ssh_any" -eq 0 && "$self_any" -eq 1 ]]; then
    info "workers: self-provision only; deploy_target.sh did not SSH"
  fi
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
  if ! ssh_cmd "$dest" "trap 'rm -f /tmp/goliath-release.tgz' EXIT; sudo mkdir -p '$remote_dir' && sudo find '$remote_dir' -mindepth 1 -maxdepth 1 -exec rm -rf {} + && sudo tar --no-same-owner --no-same-permissions -m -xzf /tmp/goliath-release.tgz -C '$remote_dir'"; then
    ssh_cmd "$dest" "rm -f /tmp/goliath-release.tgz" || true
    return 1
  fi
}

# The gateway host is only the REST API gateway (workers -> DB). It never gets
# the release bundle: earlier code tarred the whole GOLIATH_RELEASE_SRC
# (every worker wheel, ~1.7 GB of CUDA/RAPIDS) into <root>/release because
# stage_release_dir was shared with the runtime path. Now it receives
# <root>/gateway-dist with only the methyl-gateway / methyl-reclaim-leases
# wheels, their dependency wheels from wheels/, and a requirements file
# filtered to the gateway's own dependencies (gateway_release_subset.py).
# The archive streams into root-owned tar, so nothing large lands in the SSH
# user's /tmp. A legacy <root>/release tree is deleted.
stage_gateway_release() {
  local dest="$1" root="$2"
  local src="${GOLIATH_RELEASE_SRC:-}" slim="$stage_dir/gateway-dist" bundle="$stage_dir/gateway-dist.tgz"
  local need_kb remote
  [[ -n "$src" ]] || return 0
  [[ -d "$src" ]] || die "GOLIATH_RELEASE_SRC is not a directory: $src"
  [[ -f "$src/manifest.json" ]] || die "GOLIATH_RELEASE_SRC has no manifest.json: $src"
  rm -rf "$slim"
  python3 "$REPO_ROOT/scripts/gateway_release_subset.py" "$src" "$slim" >&2 || return 1
  tar -C "$slim" -czf "$bundle" . || return 1
  # Extracted size plus 64 MiB headroom for the venv upgrade.
  need_kb=$(( $(du -sk "$slim" | awk '{print $1}') + 65536 ))
  rm -rf "$slim"
  info "staging gateway wheels ($(du -h "$bundle" | awk '{print $1}') compressed) -> ${dest}:${root}/gateway-dist"
  remote="set -e; R='$root'; NEED=$need_kb
sudo mkdir -p \"\$R\"
rm -f /tmp/goliath-release.tgz
for old in \"\$R/release\" \"\$R/releases\" \"\$R/gateway-dist.incoming\"; do
  if [ -e \"\$old\" ] || [ -L \"\$old\" ]; then echo \"[INFO] removing \$old (the gateway does not keep release bundles)\" >&2; sudo rm -rf \"\$old\"; fi
done
AVAIL=\$(df -Pk \"\$R\" | awk 'NR==2 {print \$4}')
if [ \"\$AVAIL\" -lt \"\$NEED\" ]; then
  echo \"[ERROR] not enough space on \$(hostname) for the gateway wheels: need \$((NEED / 1024)) MiB under \$R, \$((AVAIL / 1024)) MiB free\" >&2
  exit 3
fi
sudo mkdir -p \"\$R/gateway-dist.incoming\"
if ! sudo tar --no-same-owner --no-same-permissions -m -xzf - -C \"\$R/gateway-dist.incoming\"; then
  sudo rm -rf \"\$R/gateway-dist.incoming\"
  echo \"[ERROR] extracting the gateway wheels on \$(hostname) failed (disk space or quota under \$R?)\" >&2
  exit 4
fi
sudo rm -rf \"\$R/gateway-dist\"
sudo mv \"\$R/gateway-dist.incoming\" \"\$R/gateway-dist\""
  if ! ssh_cmd "$dest" "$remote" <"$bundle"; then
    rm -f "$bundle"
    echo "[ERROR] staging the gateway wheels on ${dest} failed; see the remote message above (free space / quota)" >&2
    return 1
  fi
  rm -f "$bundle"
}

run_runtime() {
  local root release pull_flag count i dest arch arch_flag arch_label self
  local -a ssh_dest=() ssh_arch=()
  local self_count=0
  root="$(jq_get '.runtime.root // "/work/goliath"')"
  release="$(jq_get '.runtime.release_dir // empty')"
  if [[ "$release" == "null" ]]; then
    release=""
  fi
  if [[ -n "${GOLIATH_RELEASE_SRC:-}" ]]; then
    [[ -d "$GOLIATH_RELEASE_SRC" ]] || die "GOLIATH_RELEASE_SRC is not a directory: $GOLIATH_RELEASE_SRC"
    [[ -f "$GOLIATH_RELEASE_SRC/manifest.json" ]] || die "GOLIATH_RELEASE_SRC has no manifest.json: $GOLIATH_RELEASE_SRC"
  fi
  run_runtime_schema || return 1
  if [[ "$(jq_get '.runtime.pull_parabricks // false')" == "true" ]]; then
    pull_flag="--pull-parabricks"
  else
    pull_flag="--skip-docker-pull"
  fi
  if jq -e '.runtime.hosts | type == "array" and length > 0' "$PROFILE" >/dev/null; then
    count="$(jq_get '.runtime.hosts | length')"
    for ((i = 0; i < count; i++)); do
      dest="$(jq_get ".runtime.hosts[$i].ssh")"
      # Arch belongs to this host. Do not borrow workers[$i] — the lists can
      # differ in length and order. Detect on the host when it is unset.
      arch="$(jq_get ".runtime.hosts[$i].arch // empty")"
      if [[ -z "$arch" || "$arch" == "null" ]]; then
        arch="$(jq -r --arg ssh "$dest" '[.workers[]? | select(.ssh == $ssh) | .arch] | first // empty' "$PROFILE")"
      fi
      if [[ -z "$arch" || "$arch" == "null" ]]; then
        arch=""
      fi
      ssh_dest+=("$dest")
      ssh_arch+=("$arch")
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
      dest="$(jq_get ".workers[$i].ssh")"
      arch="$(jq_get ".workers[$i].arch")"
      if [[ -z "$arch" || "$arch" == "null" ]]; then
        arch=""
      fi
      ssh_dest+=("$dest")
      ssh_arch+=("$arch")
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
    arch_flag=""
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
    # release_dir is one shared /work tree. Extract it on the first host
    # only, before promote points current at it. A later host must not
    # empty that live directory.
    if [[ "$i" -eq 0 ]]; then
      if ! stage_release_dir "$dest" "$release"; then
        report_component failed goliathomics-runtime "stage ${arch_label} ${dest} failed"
        return 1
      fi
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
