#!/bin/bash
# Write worker.env and parabricks.env from a release manifest.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/write_worker_env.sh [options]

Options:
  --root PATH          GoliathOmics root (default: /work/goliath)
  --manifest PATH      manifest.json (default: <root>/current/manifest.json)
  --arch KEY           aarch64 or amd64 (default: detect from uname)
  --worker-api-base URL  WORKER_API_BASE (env, then existing worker.env; never invent a URL)
  -h, --help           Show this help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=methyl_extractor_local.sh
source "$SCRIPT_DIR/methyl_extractor_local.sh"
# shellcheck source=worker_venv_local.sh
source "$SCRIPT_DIR/worker_venv_local.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
MANIFEST=""
ARCH=""
WORKER_API_BASE="${WORKER_API_BASE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --worker-api-base) WORKER_API_BASE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

die() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

# Promote/CI often omit WORKER_API_BASE. cat > would otherwise delete a real URL
# and systemd --api-base ${WORKER_API_BASE} would expand empty → localhost:8080.
read_existing_worker_api_base() {
  local file="$1"
  local line value
  [[ -f "$file" ]] || return 0
  line="$(grep -E '^[[:space:]]*WORKER_API_BASE=' "$file" | tail -1 || true)"
  [[ -n "$line" ]] || return 0
  value="${line#*=}"
  value="${value%$'\r'}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  printf '%s' "$value"
}

# Keep keys this script does not write (API storage, capability, Rollouts env).
preserve_unset_keys() {
  local old="$1"
  local new="$2"
  local line key
  [[ -f "$old" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if grep -qE "^[[:space:]]*${key}=" "$new"; then
      continue
    fi
    # A shared Runpod volume is readable by every pod. Do not copy credential
    # lines back into the shared worker.env.
    if [[ "${GOLIATH_SHARED_WORK:-}" == "1" ]]; then
      case "$key" in
        *PASSWORD*|*SECRET*|*TOKEN*|*ACCESS_KEY*|*PRIVATE_KEY*|*CREDENTIAL*)
          continue
          ;;
      esac
    fi
    printf '%s\n' "$line" >>"$new"
  done <"$old"
}

_worker_env_snapshot=""
cleanup_worker_env_snapshot() {
  if [[ -n "${_worker_env_snapshot:-}" ]]; then
    rm -f "$_worker_env_snapshot"
    _worker_env_snapshot=""
  fi
}

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
CURRENT_DIR="$(goliath_current_dir "$ROOT" 2>/dev/null || true)"
MANIFEST="${MANIFEST:-${CURRENT_DIR:-$ROOT/current}/manifest.json}"
[[ -f "$MANIFEST" ]] || die "Manifest not found: $MANIFEST"

read_manifest() {
  python3 - "$MANIFEST" "$ARCH" <<'PY'
import json, sys
path, arch = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    m = json.load(f)
art = (m.get("artifacts") or {}).get(arch) or {}
print(m.get("parabricks_image", ""))
print(m.get("docker_data_root", f"/work/goliath/docker"))
print(m.get("version", ""))
PY
}

mapfile -t _lines < <(read_manifest)
PARABRICKS_IMAGE="${_lines[0]:-$(resolve_parabricks_image)}"
DOCKER_DATA_ROOT="${_lines[1]:-/work/goliath/docker}"
# Overlay graph storage does not work on a Runpod global volume. The Runpod
# start script sets GOLIATH_SHARED_WORK=1 so the runtime data-root stays on
# container-local disk and ${ROOT}/images is only a shared image cache.
if [[ "${GOLIATH_SHARED_WORK:-}" == "1" ]]; then
  DOCKER_DATA_ROOT="/var/lib/docker"
fi
RELEASE_VERSION="${_lines[2]:-unknown}"

# The worker venv is on local disk, never on the shared volume.
VENV_DIR="$(worker_venv_local_dir "$ARCH")"
# GoliathExtractor runs from local disk, never from the shared volume.
ME_ROOT="$(methyl_extractor_local_home "$ARCH")"
ME_BIN="$(_mel_bin_in_home "$ME_ROOT" || true)"
PLUGIN_DIR="$ME_ROOT/lib/hdf5_zstd_plugin"
# MethylExtractor resolves its bundled libs through RUNPATH; <home>/lib must
# never be on LD_LIBRARY_PATH (it made /usr/bin/curl load the tarball's
# Ubuntu 22.04 libcurl). EnvironmentFile= is literal, so write the current
# value now, without that directory; an empty value overrides a stale one.
ME_LD_PATH="$(_mel_ld_path_without "$ME_ROOT/lib")"

if [[ -z "$ME_BIN" ]]; then
  ME_BIN="${GOLIATH_EXTRACTOR_BIN:-${METHYL_EXTRACTOR_BIN:-goliath-extractor}}"
  PLUGIN_DIR="${HDF5_PLUGIN_PATH:-/usr/local/hdf5/lib/plugin}"
fi

ENV_DIR="$ROOT/env"
mkdir -p "$ENV_DIR"
WORKER_PATH="$(expand_worker_path "$VENV_DIR")"
if [[ -x "$ME_ROOT/bin/goliath-extractor" || -x "$ME_ROOT/bin/MethylExtractor" ]] \
  && [[ ":$WORKER_PATH:" != *":$ME_ROOT/bin:"* ]]; then
  WORKER_PATH="$ME_ROOT/bin:$WORKER_PATH"
fi
WORKER_PATH="$(ensure_parabricks_on_path "$WORKER_PATH")"

if [[ -z "$WORKER_API_BASE" ]]; then
  WORKER_API_BASE="$(read_existing_worker_api_base "$ENV_DIR/worker.env")"
  if [[ -n "$WORKER_API_BASE" ]]; then
    info "Keeping existing WORKER_API_BASE from $ENV_DIR/worker.env"
  fi
fi

# Snapshot before the rewrite. A later runtime-only promote must not drop
# storage keys that deploy_target.sh merged into this file.
trap cleanup_worker_env_snapshot EXIT
if [[ -f "$ENV_DIR/worker.env" ]]; then
  _worker_env_snapshot="$(mktemp)"
  chmod 600 "$_worker_env_snapshot"
  cat "$ENV_DIR/worker.env" >"$_worker_env_snapshot"
fi

_prev_umask="$(umask)"
# Shared /work is world-readable. A shared group, when it exists on this
# machine, makes the file group-writable (0664, umask 0002). A volume that
# rejects chmod keeps the create mode. The non-shared path stays mode 0600.
_shared_file_mode=644
if [[ "${GOLIATH_SHARED_WORK:-}" == "1" ]]; then
  if [[ -n "${GOLIATH_SHARED_GROUP:-}" ]] && getent group "$GOLIATH_SHARED_GROUP" >/dev/null 2>&1; then
    umask 002
    _shared_file_mode=664
  else
    umask 022
  fi
else
  umask 077
fi
cat >"$ENV_DIR/worker.env" <<EOF
# Generated by scripts/write_worker_env.sh (release ${RELEASE_VERSION})
# EnvironmentFile= does not expand variables — PATH must be literal.
GOLIATH_ROOT=$ROOT
GOLIATH_RELEASE=$RELEASE_VERSION
${WORKER_API_BASE:+WORKER_API_BASE=$WORKER_API_BASE}
DOCKER_DATA_ROOT=$DOCKER_DATA_ROOT
${GOLIATH_SHARED_WORK:+GOLIATH_IMAGE_CACHE=$ROOT/images}
# WORKER_ID=
# WORKER_TOKEN=  (enrollment identity; bearer session via POST /v1/workers/session)
# WORKER_CAPABILITY=
GOLIATH_EXTRACTOR_HOME=$ME_ROOT
GOLIATH_EXTRACTOR_BIN=$ME_BIN
METHYL_EXTRACTOR_HOME=$ME_ROOT
METHYL_EXTRACTOR_BIN=$ME_BIN
HDF5_PLUGIN_PATH=$PLUGIN_DIR
LD_LIBRARY_PATH=$ME_LD_PATH
PATH=$WORKER_PATH
METHYL_PROFILE_DIR=$(dirname "$(readlink -f "$MANIFEST" 2>/dev/null || printf %s "$MANIFEST")")/runtime-bundle/domain/profiles
TMPDIR=/var/tmp/methyl-samtools
# Sample CAAS is a ledger (in-place FASTQ/BAM). =0 stops older worker wheels
# from shutil.move-ing multi-GB products into .caas/ on NFS.
METHYL_SAMPLE_CAAS_ENABLED=0
EOF

if [[ -n "$PARABRICKS_IMAGE" ]]; then
  echo "METHYL_PARABRICKS_IMAGE=$PARABRICKS_IMAGE" >>"$ENV_DIR/worker.env"
  # Quote: unquoted `--gpus all` breaks `set -a; source worker.env` (runs `all`).
  echo 'METHYL_PARABRICKS_GPU_FLAGS="--gpus all"' >>"$ENV_DIR/worker.env"
fi

# Sage DDA (CPU, proteomics.sage) — advertise on any worker (incl. non-GPU) when set.
SAGE_IMAGE="${METHYL_SAGE_IMAGE:-$(resolve_proteomics_image sage 2>/dev/null || true)}"
if [[ -n "$SAGE_IMAGE" ]]; then
  echo "METHYL_SAGE_IMAGE=$SAGE_IMAGE" >>"$ENV_DIR/worker.env"
fi

# methylGrapher WGBS image pull/load helper only — science knobs live in
# actionConfig.methylgrapher_wgbs → task resolvedConfig (not worker.env).
METHYLGRAPHER_IMAGE="${METHYL_METHYLGRAPHER_IMAGE:-$(resolve_methylgrapher_image 2>/dev/null || true)}"
if [[ -n "$METHYLGRAPHER_IMAGE" ]]; then
  echo "METHYL_METHYLGRAPHER_IMAGE=$METHYLGRAPHER_IMAGE" >>"$ENV_DIR/worker.env"
fi

# GPU HBM admission / post-action release (host flock; not science resolvedConfig).
# Prefer free-fraction of total HBM; absolute GiB is legacy-only.
HBM_FRAC="${METHYL_GPU_HBM_FREE_FRACTION:-0.90}"
HBM_WAIT="${METHYL_GPU_ALIGN_HBM_WAIT_S:-180}"
{
  echo "METHYL_GPU_HBM_FREE_FRACTION=$HBM_FRAC"
  echo "METHYL_GPU_ALIGN_HBM_WAIT_S=$HBM_WAIT"
} >>"$ENV_DIR/worker.env"

if [[ -n "${_worker_env_snapshot:-}" ]]; then
  preserve_unset_keys "$_worker_env_snapshot" "$ENV_DIR/worker.env"
  cleanup_worker_env_snapshot
fi
if [[ "${GOLIATH_SHARED_WORK:-}" == "1" ]]; then
  if ! chmod "$_shared_file_mode" "$ENV_DIR/worker.env"; then
    info "warning: chmod ${_shared_file_mode} $ENV_DIR/worker.env was not applied; continuing"
  fi
  if [[ "$_shared_file_mode" == "664" ]]; then
    if ! chgrp "$GOLIATH_SHARED_GROUP" "$ENV_DIR/worker.env"; then
      info "warning: chgrp $GOLIATH_SHARED_GROUP $ENV_DIR/worker.env was not applied; continuing"
    fi
  fi
else
  chmod 600 "$ENV_DIR/worker.env"
fi
umask "$_prev_umask"

cat >"$ENV_DIR/parabricks.env" <<EOF
# Generated by scripts/write_worker_env.sh
METHYL_PARABRICKS_IMAGE=${PARABRICKS_IMAGE:-}
METHYL_PARABRICKS_GPU_FLAGS="--gpus all"
EOF

if [[ "${GOLIATH_SHARED_WORK:-}" == "1" ]]; then
  if ! chmod "$_shared_file_mode" "$ENV_DIR/parabricks.env"; then
    info "warning: chmod ${_shared_file_mode} $ENV_DIR/parabricks.env was not applied; continuing"
  fi
fi
info "Wrote $ENV_DIR/worker.env"
info "Wrote $ENV_DIR/parabricks.env"
