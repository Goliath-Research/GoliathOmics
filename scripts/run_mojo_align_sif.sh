#!/usr/bin/env bash
# Run the mojo-align Apptainer image the same way the Docker image runs.
#
# The .sif is read-only. Mojo writes its compile cache under MODULAR_HOME
# (crashdb, .max_cache) and MODULAR_CACHE_DIR (/tmp/modular_cache). Those
# three paths are bind-mounted from a host cache. The container process is
# the calling user, so files it creates on a bind of /work are owned by that
# user. umask 002 keeps them group-writable.
#
# GPU: --nv (NVIDIA) or --rocm (AMD), matching docker --gpus all /
# --device=/dev/kfd. Host LD_LIBRARY_PATH and MODULAR_HOME are not imported
# (--cleanenv). METHYLGRAPHER_* and the visible-device variables are.
#
# Usage:
#   scripts/run_mojo_align_sif.sh IMAGE.sif help
#   scripts/run_mojo_align_sif.sh IMAGE.sif --gpu nvidia --work /work -- Align ...
#   scripts/run_mojo_align_sif.sh IMAGE.sif --raw -- vg version
#
# Env:
#   MOJO_ALIGN_SIF_CACHE   host dir for the Mojo cache (default ~/.cache/mojo-align/<sif>)
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/run_mojo_align_sif.sh IMAGE.sif [options] [--] [methylGrapher args...]

Options:
  --gpu auto|nvidia|amd|cpu   auto uses nvidia-smi, then rocm-smi (default: auto)
  --work PATH                 Bind PATH at the same path inside the image (default: /work if it exists)
  --bind HOST[:DEST]          Extra bind. Repeatable.
  --raw                       Run the remaining arguments as the container command
                              instead of prefixing methylGrapher.
EOF
}

if [[ $# -lt 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SIF="$1"
shift
if [[ ! -f "$SIF" ]]; then
  echo "ERROR: .sif not found: $SIF" >&2
  exit 1
fi

GPU="auto"
WORK="/work"
RAW=0
EXTRA_BINDS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) GPU="${2:-}"; shift 2 ;;
    --work) WORK="${2:-}"; shift 2 ;;
    --bind) EXTRA_BINDS+=("${2:-}"); shift 2 ;;
    --raw) RAW=1; shift ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
done

if [[ "$RAW" -eq 0 && $# -eq 0 ]]; then
  set -- help
fi
if [[ "$RAW" -eq 1 && $# -eq 0 ]]; then
  echo "ERROR: --raw requires a container command" >&2
  exit 1
fi

APPTAINER=""
if command -v apptainer >/dev/null 2>&1; then
  APPTAINER="apptainer"
elif command -v singularity >/dev/null 2>&1; then
  APPTAINER="singularity"
else
  echo "ERROR: apptainer (or singularity) is not on PATH" >&2
  exit 1
fi

resolve_gpu() {
  case "$GPU" in
    nvidia|amd|cpu|auto) ;;
    *)
      echo "ERROR: --gpu must be auto, nvidia, amd, or cpu (got ${GPU})" >&2
      exit 1
      ;;
  esac
  if [[ "$GPU" == "auto" ]]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
      GPU="nvidia"
    elif command -v rocm-smi >/dev/null 2>&1 && rocm-smi --showproductname >/dev/null 2>&1; then
      GPU="amd"
    elif command -v rocminfo >/dev/null 2>&1 && rocminfo >/dev/null 2>&1; then
      GPU="amd"
    else
      GPU="cpu"
    fi
  fi
}

resolve_gpu
GPU_ARGS=()
case "$GPU" in
  nvidia) GPU_ARGS=(--nv) ;;
  amd) GPU_ARGS=(--rocm) ;;
  cpu) ;;
esac

CACHE_ID="$(basename "$SIF" .sif)"
CACHE_ROOT="${MOJO_ALIGN_SIF_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/mojo-align/${CACHE_ID}}"
mkdir -p \
  "${CACHE_ROOT}/crashdb" \
  "${CACHE_ROOT}/max_cache" \
  "${CACHE_ROOT}/modular_cache"

BIND_ARGS=(
  --bind "${CACHE_ROOT}/crashdb:/opt/mojo-align/mojo-env/share/max/crashdb"
  --bind "${CACHE_ROOT}/max_cache:/opt/mojo-align/mojo-env/share/max/.max_cache"
  --bind "${CACHE_ROOT}/modular_cache:/tmp/modular_cache"
)
if [[ -n "$WORK" && -d "$WORK" ]]; then
  BIND_ARGS+=(--bind "${WORK}:${WORK}")
fi
for spec in "${EXTRA_BINDS[@]}"; do
  BIND_ARGS+=(--bind "$spec")
done

# Match docker run: do not inherit host linker or Modular paths.
ENV_ARGS=(--cleanenv)
while IFS= read -r key; do
  case "$key" in
    METHYLGRAPHER_*|CUDA_VISIBLE_DEVICES|HIP_VISIBLE_DEVICES|ROCR_VISIBLE_DEVICES)
      ENV_ARGS+=(--env "${key}=${!key}")
      ;;
  esac
done < <(compgen -e | sort)

if [[ "$RAW" -eq 1 ]]; then
  exec "$APPTAINER" exec \
    "${GPU_ARGS[@]}" \
    "${ENV_ARGS[@]}" \
    "${BIND_ARGS[@]}" \
    --pwd /opt/mojo-align \
    "$SIF" \
    "$@"
fi

exec "$APPTAINER" exec \
  "${GPU_ARGS[@]}" \
  "${ENV_ARGS[@]}" \
  "${BIND_ARGS[@]}" \
  --pwd /opt/mojo-align \
  "$SIF" \
  sh -c 'umask 002; exec methylGrapher "$@"' sh "$@"
