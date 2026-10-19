#!/usr/bin/env bash
# Variables are shared between create_worker.sh and the sourced provider hooks.
# shellcheck disable=SC2034
# Create a GoliathOmics worker on any cloud provider with the shared storage
# mounted at /work.
#
#   deploy/workers/create_worker.sh --provider <name> [options]
#
# This script owns the provider-neutral flow: option parsing, the next free
# goliath-worker-N name, the /work mount policy, the price display and the
# --yes gate, --dry-run, verifying the mount after creation (never deleting
# anything automatically), and the key=value summary. Everything that depends on
# a cloud (APIs, credentials, request shapes) lives in providers/<name>.sh, which
# overrides the hooks declared in providers/_interface.sh. See
# providers/README.md to add a provider.
#
# Creating a worker is billable, so it needs --yes; --dry-run resolves
# everything with read-only calls and prints the request.

set +x
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROVIDERS_DIR="${CREATE_WORKER_PROVIDERS_DIR:-$HERE/providers}"
STANDARD_MOUNT_PATH=/work
NAME_PREFIX=goliath-worker

PROVIDER=""
NAME=""
OPT_TEMPLATE="" OPT_STORAGE="" OPT_GPU="" OPT_CLOUD="" OPT_IMAGE="" OPT_REGISTRY_AUTH=""
OPT_TEMPLATE_SET=0 OPT_STORAGE_SET=0 OPT_GPU_SET=0 OPT_CLOUD_SET=0 OPT_IMAGE_SET=0 OPT_REGISTRY_AUTH_SET=0
OPT_GPU_COUNT=1
MOUNT_PATH="$STANDARD_MOUNT_PATH"
DRY_RUN=0
YES=0
ALLOW_NONSTANDARD_PATH=0
WAIT_SECS="${CREATE_WORKER_WAIT_SECS:-${CREATE_WORKER_POD_WAIT_SECS:-180}}"
POLL_SECS="${CREATE_WORKER_POLL_SECS:-${CREATE_WORKER_POD_POLL_SECS:-10}}"

log() { printf '[create-worker] %s\n' "$*" >&2; }
die() { printf '[create-worker] ERROR: %s\n' "$*" >&2; exit 1; }

available_providers() {
  local f out=""
  for f in "$PROVIDERS_DIR"/*.sh; do
    [[ -e "$f" ]] || continue
    f="$(basename "$f" .sh)"
    [[ "$f" == _* ]] && continue
    out+="${out:+, }$f"
  done
  printf '%s' "${out:-none}"
}

usage() {
  cat <<USAGE
Usage: $(basename "$0") --provider NAME [options]

Create a goliath-worker with the shared storage mounted at /work.

  --provider NAME           Cloud provider (available: $(available_providers))
  --name NAME               Worker name (default: next free goliath-worker-N)
  --template NAME_OR_ID     Image/template (provider default)
  --image IMAGE            Override container image (provider-specific; see providers/README.md)
  --registry-auth ID       Private registry credential id (provider-specific)
  --volume NAME_OR_ID       Shared storage to mount (provider default)
  --mount-path PATH         Mount path (default: /work; anything else also
                            needs --allow-nonstandard-path)
  --allow-nonstandard-path  Allow a --mount-path other than /work
  --gpu GPU_TYPE            GPU / instance type (provider default)
  --gpu-count N             GPUs per worker (default: 1)
  --cloud TIER              Provider-specific cloud tier (provider default)
  --wait SECS               Seconds to wait for SSH / public IP (default: 180)
  --dry-run                 Resolve everything, print the request, create nothing
  --yes                     Actually create the worker (billable)
  -h, --help                Show this help

Provider credentials come from the environment; see providers/<name>.sh.
USAGE
}

need_arg() { [[ $# -ge 2 && -n "$2" ]] || die "$1 needs a value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider) need_arg "$@"; PROVIDER="$2"; shift 2 ;;
    --name) need_arg "$@"; NAME="$2"; shift 2 ;;
    --template) need_arg "$@"; OPT_TEMPLATE="$2"; OPT_TEMPLATE_SET=1; shift 2 ;;
    --image) need_arg "$@"; OPT_IMAGE="$2"; OPT_IMAGE_SET=1; shift 2 ;;
    --registry-auth) need_arg "$@"; OPT_REGISTRY_AUTH="$2"; OPT_REGISTRY_AUTH_SET=1; shift 2 ;;
    --volume) need_arg "$@"; OPT_STORAGE="$2"; OPT_STORAGE_SET=1; shift 2 ;;
    --mount-path) need_arg "$@"; MOUNT_PATH="$2"; shift 2 ;;
    --allow-nonstandard-path) ALLOW_NONSTANDARD_PATH=1; shift ;;
    --gpu) need_arg "$@"; OPT_GPU="$2"; OPT_GPU_SET=1; shift 2 ;;
    --gpu-count) need_arg "$@"; OPT_GPU_COUNT="$2"; shift 2 ;;
    --cloud) need_arg "$@"; OPT_CLOUD="$2"; OPT_CLOUD_SET=1; shift 2 ;;
    --wait) need_arg "$@"; WAIT_SECS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

# ---- load the provider (abstract interface first, then the overrides) ----
[[ -n "$PROVIDER" ]] || { usage >&2; die "--provider is required (available: $(available_providers))"; }
[[ "$PROVIDER" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "--provider must be lowercase letters, digits, '-' or '_' (got '$PROVIDER')"
PROVIDER_FILE="$PROVIDERS_DIR/$PROVIDER.sh"
[[ -f "$PROVIDER_FILE" ]] || die "unknown provider '$PROVIDER' (available: $(available_providers))"
# shellcheck source=providers/_interface.sh
source "$PROVIDERS_DIR/_interface.sh"
# shellcheck disable=SC1090
source "$PROVIDER_FILE"

provider_defaults
[[ "$OPT_TEMPLATE_SET" == 1 ]] || OPT_TEMPLATE="${PROVIDER_DEFAULT_TEMPLATE:-}"
[[ "$OPT_STORAGE_SET" == 1 ]] || OPT_STORAGE="${PROVIDER_DEFAULT_STORAGE:-}"
[[ "$OPT_GPU_SET" == 1 ]] || OPT_GPU="${PROVIDER_DEFAULT_GPU:-}"
[[ "$OPT_CLOUD_SET" == 1 ]] || OPT_CLOUD="${PROVIDER_DEFAULT_CLOUD:-}"
[[ "$OPT_IMAGE_SET" == 1 ]] || OPT_IMAGE="${PROVIDER_DEFAULT_IMAGE:-${CREATE_WORKER_IMAGE:-}}"
[[ "$OPT_REGISTRY_AUTH_SET" == 1 ]] || OPT_REGISTRY_AUTH="${PROVIDER_DEFAULT_REGISTRY_AUTH:-${CREATE_WORKER_REGISTRY_AUTH:-}}"

# ---- static checks (no network) ----
command -v jq >/dev/null 2>&1 || die "jq is required"
[[ "$OPT_GPU_COUNT" =~ ^[1-9][0-9]*$ ]] || die "--gpu-count must be a positive integer (got '$OPT_GPU_COUNT')"
[[ "$WAIT_SECS" =~ ^[0-9]+$ ]] || die "--wait must be a non-negative integer"
[[ "$POLL_SECS" =~ ^[1-9][0-9]*$ ]] || die "CREATE_WORKER_POLL_SECS must be a positive integer"
[[ "$MOUNT_PATH" == /* ]] || die "--mount-path must be an absolute path (got '$MOUNT_PATH')"
MOUNT_PATH="${MOUNT_PATH%/}"
[[ -n "$MOUNT_PATH" ]] || die "--mount-path cannot be /"
if [[ "$MOUNT_PATH" != "$STANDARD_MOUNT_PATH" && "$ALLOW_NONSTANDARD_PATH" != 1 ]]; then
  die "--mount-path $MOUNT_PATH is not $STANDARD_MOUNT_PATH; the worker bootstrap needs the volume at $STANDARD_MOUNT_PATH (pass --allow-nonstandard-path to override)"
fi
if [[ -n "$NAME" && ! "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  die "--name must be letters, digits, '.', '_' or '-' (got '$NAME')"
fi

CW_TMP="$(umask 077 && mktemp -d)"
trap 'rm -rf "$CW_TMP"' EXIT

provider_require_env

# ---- resolve template and shared storage ----
TEMPLATE_ID=""
STORAGE_ID=""
provider_resolve_template
[[ -n "$TEMPLATE_ID" ]] || die "provider $PROVIDER did not resolve a template"
provider_resolve_storage
[[ -n "$STORAGE_ID" ]] || die "provider $PROVIDER did not resolve the shared storage"

# ---- pick the worker name ----
provider_list_worker_names >"$CW_TMP/names.txt"
name_taken() { grep -qxF -- "$1" "$CW_TMP/names.txt"; }
if [[ -z "$NAME" ]]; then
  n=1
  while name_taken "$NAME_PREFIX-$n"; do n=$((n + 1)); done
  NAME="$NAME_PREFIX-$n"
elif name_taken "$NAME"; then
  die "a worker named '$NAME' already exists; pick another --name"
fi
WORKER_NAME="$NAME"
log "worker name: $WORKER_NAME"

# ---- price ----
provider_price

# ---- request ----
REQUEST="$CW_TMP/request.json"
provider_build_request "$REQUEST"
# Belt and braces: never send a request without the shared mount at the path.
provider_check_request "$REQUEST" || die "internal error: request lacks the shared storage $STORAGE_ID at $MOUNT_PATH"

if [[ "$DRY_RUN" == 1 ]]; then
  log "dry run: $(provider_request_target) would send:"
  jq . "$REQUEST"
  exit 0
fi

if [[ "$YES" != 1 ]]; then
  jq . "$REQUEST" >&2
  die "creating a worker is billable; re-run with --yes to create it (or --dry-run to only print the request)"
fi

# ---- create ----
log "creating $WORKER_NAME ..."
WORKER_ID=""
provider_create "$REQUEST"
[[ -n "$WORKER_ID" ]] || die "provider $PROVIDER created no worker id; check the provider console for a worker named $WORKER_NAME"

# ---- verify the mount ----
W_NAME="" W_STATUS="" W_SSH="" W_PUBLIC_IP="" W_MOUNTS="" W_MOUNTS_RAW=""
got=0
for _ in 1 2 3 4 5; do
  if provider_describe "$WORKER_ID"; then got=1; break; fi
  sleep "${CREATE_WORKER_DESCRIBE_RETRY_SECS:-3}"
done
if [[ "$got" != 1 ]]; then
  die "could not read worker $WORKER_ID to verify its mounts; check it in the provider console and terminate it if $MOUNT_PATH is not the shared storage"
fi
mount_ok() {
  local line
  while IFS= read -r line; do
    line="${line%/}"
    [[ "$line" == "$STORAGE_ID:$MOUNT_PATH" ]] && return 0
  done <<<"$W_MOUNTS"
  return 1
}
if ! mount_ok; then
  log "ERROR: worker $WORKER_ID ($WORKER_NAME) does not have shared storage $STORAGE_ID mounted at $MOUNT_PATH"
  log "ERROR: mounts reported: ${W_MOUNTS_RAW:-${W_MOUNTS:-none}}"
  log "ERROR: this worker cannot run the pipeline and keeps billing. Terminate it (it was NOT terminated automatically):"
  while IFS= read -r line; do log "ERROR:   $line"; done < <(provider_terminate_hint "$WORKER_ID")
  exit 3
fi
log "verified: shared storage $STORAGE_ID mounted at $MOUNT_PATH"

# ---- wait for SSH and summary ----
provider_wait_ready "$WORKER_ID" || true

printf 'pod_id=%s\n' "$WORKER_ID"
printf 'name=%s\n' "$W_NAME"
printf 'status=%s\n' "$W_STATUS"
printf 'mount=%s:%s\n' "$STORAGE_ID" "$MOUNT_PATH"
printf 'ssh=%s\n' "${W_SSH:-<not ready yet; describe worker $WORKER_ID later>}"
printf 'public_ip=%s\n' "${W_PUBLIC_IP:-<not assigned yet>}"
