#!/usr/bin/env bash
# Build goliath/methylgrapher:1.70-mojo from mojo-align.
#
# MOJO_ALIGN_ROOT must point at the mojo-align checkout. When unset,
# auto-detect uses the sibling ../mojo-align next to this GoliathOmics repo.
#
# Stages into the image under /opt/mojo-align:
#   - engine/          patched Python package
#   - src/             native Mojo CLI + MethylCall hot path
#   - mojo-env/        trimmed Mojo 1.0 runtime from the repo pixi env
#
# When ROOT is the mojo-align package layout (gpu-common/, fq2bam-meth/, …),
# this script runs scripts/stage_flat_image_tree.sh first so Docker still
# COPY engine/ + src/ into /opt/mojo-align.
#
# Reuses vg.arm64 / vg_libs from a prior scripts/build_methylgrapher_image.sh run.
#
# After the Docker smoke, pack the same image as an Apptainer .sif when
# apptainer (or singularity) is on PATH. MOJO_ALIGN_BUILD_SIF=skip leaves
# the image as Docker only; =require fails the build if the tool is missing.
# Run the .sif with scripts/run_mojo_align_sif.sh (not docker run).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKER_DIR="${REPO_ROOT}/workers/docker/methylgrapher"

# GPU variant selects Dockerfile label + default tag suffix (cuda|rocm).
# Dual-ship: build twice with MOJO_ALIGN_GPU_VARIANT=cuda|rocm and
# MOJO_ALIGN_IMAGE_TAG=1.70-mojo-cuda|1.70-mojo-rocm.
GPU_VARIANT="${MOJO_ALIGN_GPU_VARIANT:-}"
if [[ -z "${GPU_VARIANT}" && -n "${METHYLGRAPHER_MOJO_GPU_VARIANT:-}" ]]; then
  echo "warning: METHYLGRAPHER_MOJO_GPU_VARIANT is deprecated; use MOJO_ALIGN_GPU_VARIANT" >&2
  GPU_VARIANT="${METHYLGRAPHER_MOJO_GPU_VARIANT}"
fi
GPU_VARIANT="${GPU_VARIANT:-cuda}"
case "${GPU_VARIANT}" in
  cuda|rocm) ;;
  *)
    echo "ERROR: MOJO_ALIGN_GPU_VARIANT must be cuda or rocm (got ${GPU_VARIANT})" >&2
    exit 1
    ;;
esac
IMAGE_TAG="${MOJO_ALIGN_IMAGE_TAG:-}"
if [[ -z "${IMAGE_TAG}" && -n "${METHYLGRAPHER_MOJO_IMAGE_TAG:-}" ]]; then
  echo "warning: METHYLGRAPHER_MOJO_IMAGE_TAG is deprecated; use MOJO_ALIGN_IMAGE_TAG" >&2
  IMAGE_TAG="${METHYLGRAPHER_MOJO_IMAGE_TAG}"
fi
IMAGE_TAG="${IMAGE_TAG:-1.70-mojo-${GPU_VARIANT}}"
# Backward-compatible default when callers still request :1.70-mojo (CUDA twin).
if [[ -n "${METHYL_MOJO_ALIGN_IMAGE:-}" ]]; then
  IMAGE="${METHYL_MOJO_ALIGN_IMAGE}"
elif [[ -n "${METHYL_METHYLGRAPHER_MOJO_IMAGE:-}" ]]; then
  echo "warning: METHYL_METHYLGRAPHER_MOJO_IMAGE is deprecated; use METHYL_MOJO_ALIGN_IMAGE" >&2
  IMAGE="${METHYL_METHYLGRAPHER_MOJO_IMAGE}"
elif [[ "${IMAGE_TAG}" == "1.70-mojo" ]]; then
  IMAGE="goliath/methylgrapher:1.70-mojo"
else
  IMAGE="goliath/methylgrapher:${IMAGE_TAG}"
fi
MOJO_ROOT="${MOJO_ALIGN_ROOT:-}"
if [[ -z "${MOJO_ROOT}" && -n "${METHYLGRAPHER_MOJO_ROOT:-}" ]]; then
  echo "warning: METHYLGRAPHER_MOJO_ROOT is deprecated; use MOJO_ALIGN_ROOT" >&2
  MOJO_ROOT="${METHYLGRAPHER_MOJO_ROOT}"
fi
if [[ -z "${MOJO_ROOT}" ]]; then
  if [[ -d "${REPO_ROOT}/../mojo-align/methylgrapher/engine" ]]; then
    MOJO_ROOT="$(cd "${REPO_ROOT}/../mojo-align" && pwd)"
  else
    echo "ERROR: set MOJO_ALIGN_ROOT to the mojo-align checkout" >&2
    echo "  (expected sibling ${REPO_ROOT}/../mojo-align or an explicit path)" >&2
    exit 1
  fi
fi
if [[ ! -d "${MOJO_ROOT}/methylgrapher/engine" && ! -d "${MOJO_ROOT}/engine" ]]; then
  echo "ERROR: MOJO_ALIGN_ROOT=${MOJO_ROOT} is not a mojo-align (or flat) tree" >&2
  exit 1
fi

log() { printf '[mojo-align-image] %s\n' "$*"; }

MOJO_CHECKOUT="${MOJO_ROOT}"
# Package layout → flat tree for Dockerfile.mojo COPY paths.
if [[ -d "${MOJO_ROOT}/gpu-common/src" && -d "${MOJO_ROOT}/methylgrapher/engine" ]]; then
  FLAT="${MOJO_ROOT}/_flat_image"
  log "mojo-align package layout detected; staging flat tree at ${FLAT}"
  bash "${MOJO_ROOT}/scripts/stage_flat_image_tree.sh" "${FLAT}"
  MOJO_ROOT="${FLAT}"
fi

[[ -d "${MOJO_ROOT}/engine" ]] || { echo "missing ${MOJO_ROOT}/engine" >&2; exit 1; }
[[ -d "${MOJO_ROOT}/src" ]] || { echo "missing ${MOJO_ROOT}/src" >&2; exit 1; }
[[ -f "${DOCKER_DIR}/vg.arm64" || -f "${DOCKER_DIR}/vg" ]] || {
  echo "ERROR: vg binary missing under ${DOCKER_DIR}; run build_methylgrapher_image.sh first" >&2
  exit 1
}

VG_PREBUILT=vg.arm64
if [[ ! -f "${DOCKER_DIR}/vg.arm64" && -f "${DOCKER_DIR}/vg" ]]; then
  VG_PREBUILT=vg
fi

PIXI_ENV="${MOJO_CHECKOUT}/.pixi/envs/default"
[[ -x "${PIXI_ENV}/bin/mojo" ]] || {
  echo "ERROR: Mojo runtime missing at ${PIXI_ENV}/bin/mojo (run: cd ${MOJO_CHECKOUT} && pixi install)" >&2
  exit 1
}

log "staging engine from ${MOJO_ROOT}"
rm -rf "${DOCKER_DIR}/engine"
mkdir -p "${DOCKER_DIR}/engine"
cp -a "${MOJO_ROOT}/engine/." "${DOCKER_DIR}/engine/"
find "${DOCKER_DIR}/engine" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

log "staging src/ (native Mojo CLI + MethylCall hot path)"
rm -rf "${DOCKER_DIR}/src"
mkdir -p "${DOCKER_DIR}/src"
# Skip legacy scaffold stubs in the image.
cp -a "${MOJO_ROOT}/src/"*.mojo "${DOCKER_DIR}/src/"
# Optional DeviceContext smoke probe (used by smoke_64k.sh with --gpus).
mkdir -p "${DOCKER_DIR}/tests"
if [[ -f "${MOJO_ROOT}/tests/probe_devicecontext_cuda.mojo" ]]; then
  cp -a "${MOJO_ROOT}/tests/probe_devicecontext_cuda.mojo" "${DOCKER_DIR}/tests/"
fi

log "staging scripts/ (Giraffe GPU + GBZ helpers)"
rm -rf "${DOCKER_DIR}/scripts"
mkdir -p "${DOCKER_DIR}/scripts"
cp -a "${MOJO_ROOT}/scripts/giraffe_gpu_minimizer.py" "${DOCKER_DIR}/scripts/" 2>/dev/null || true
cp -a "${MOJO_ROOT}/scripts/gpu_seed_worker.py" "${DOCKER_DIR}/scripts/" 2>/dev/null || true
cp -a "${MOJO_ROOT}/scripts/giraffe_gaf_parity.py" "${DOCKER_DIR}/scripts/" 2>/dev/null || true
cp -a "${MOJO_ROOT}/scripts/build_mojo_gbz_cache.py" "${DOCKER_DIR}/scripts/" 2>/dev/null || true
cp -a "${MOJO_ROOT}/scripts/build_mojo_segment_pack.py" "${DOCKER_DIR}/scripts/" 2>/dev/null || true

log "staging ptxas for Mojo CUDA create (driver <580 workaround)"
rm -rf "${DOCKER_DIR}/cuda"
mkdir -p "${DOCKER_DIR}/cuda/bin"
PTXAS_SRC=""
for cand in \
  "${MODULAR_NVPTX_COMPILER_PATH:-}" \
  /usr/bin/ptxas \
  /usr/lib/cuda/bin/ptxas \
  /usr/local/cuda/bin/ptxas
do
  if [[ -n "${cand}" && -x "${cand}" ]]; then
    PTXAS_SRC="${cand}"
    break
  fi
done
if [[ -z "${PTXAS_SRC}" ]]; then
  echo "ERROR: ptxas not found on build host; required for Mojo DeviceContext(api=cuda)" >&2
  echo "Install CUDA toolkit or set MODULAR_NVPTX_COMPILER_PATH" >&2
  exit 1
fi
cp -a "${PTXAS_SRC}" "${DOCKER_DIR}/cuda/bin/ptxas"
chmod +x "${DOCKER_DIR}/cuda/bin/ptxas"
log "ptxas staged from ${PTXAS_SRC}"

log "staging trimmed Mojo runtime from pixi env"
rm -rf "${DOCKER_DIR}/mojo-env"
mkdir -p "${DOCKER_DIR}/mojo-env/bin" "${DOCKER_DIR}/mojo-env/lib/mojo" "${DOCKER_DIR}/mojo-env/share/max"
cp -a "${PIXI_ENV}/bin/mojo" "${DOCKER_DIR}/mojo-env/bin/"
# Crashpad handler required by Mojo 1.0 when running as non-root (uid 1000).
if [[ -x "${PIXI_ENV}/bin/modular-crashpad-handler" ]]; then
  cp -a "${PIXI_ENV}/bin/modular-crashpad-handler" "${DOCKER_DIR}/mojo-env/bin/"
fi
# CPython used by Mojo std.python interop (must match libpython major.minor).
if [[ -x "${PIXI_ENV}/bin/python3" ]]; then
  cp -a "${PIXI_ENV}/bin/python3" "${DOCKER_DIR}/mojo-env/bin/" || true
  # python3 may be a symlink into the env — resolve common names.
  for py in python3.13 python; do
    if [[ -e "${PIXI_ENV}/bin/${py}" ]]; then
      cp -a "${PIXI_ENV}/bin/${py}" "${DOCKER_DIR}/mojo-env/bin/" || true
    fi
  done
fi
cp -a "${PIXI_ENV}/lib/mojo/." "${DOCKER_DIR}/mojo-env/lib/mojo/"
# Runtime / compiler shared libraries used by `mojo` on aarch64.
for lib in \
  libMSupportGlobals.so \
  libAsyncRTRuntimeGlobals.so \
  libAsyncRTMojoBindings.so \
  libNVPTX.so \
  libKGENCompilerRTShared.so \
  libMGPRT.so \
  libstdc++.so.6 \
  libgcc_s.so.1 \
  libpython3.so \
  libpython3.13.so \
  libpython3.13.so.1.0
do
  if [[ -e "${PIXI_ENV}/lib/${lib}" ]]; then
    cp -a "${PIXI_ENV}/lib/${lib}" "${DOCKER_DIR}/mojo-env/lib/"
  fi
done
# Mojo Python interop (std.python) needs matching pixi CPython stdlib.
if [[ -d "${PIXI_ENV}/lib/python3.13" ]]; then
  mkdir -p "${DOCKER_DIR}/mojo-env/lib/python3.13"
  cp -a "${PIXI_ENV}/lib/python3.13/." "${DOCKER_DIR}/mojo-env/lib/python3.13/"
fi
# Rewrite modular.cfg paths to the in-container layout.
sed \
  -e "s|${PIXI_ENV}|/opt/mojo-align/mojo-env|g" \
  -e "s|${MOJO_ROOT}/.pixi/envs/default|/opt/mojo-align/mojo-env|g" \
  "${PIXI_ENV}/share/max/modular.cfg" \
  > "${DOCKER_DIR}/mojo-env/share/max/modular.cfg"
# Non-root workers (docker --user 1000:1000) must be able to write crashdb/cache.
mkdir -p "${DOCKER_DIR}/mojo-env/share/max/crashdb" \
         "${DOCKER_DIR}/mojo-env/share/max/.max_cache"
chmod -R a+rwX "${DOCKER_DIR}/mojo-env/share/max"

chmod +x "${DOCKER_DIR}/methylGrapher.mojo.sh"

log "building ${IMAGE} (gpu_variant=${GPU_VARIANT})"
docker build \
  -f "${DOCKER_DIR}/Dockerfile.mojo" \
  --build-arg "VG_PREBUILT=${VG_PREBUILT}" \
  --build-arg "GPU_VARIANT=${GPU_VARIANT}" \
  -t "${IMAGE}" \
  "${DOCKER_DIR}"

# Keep legacy :1.70-mojo as an alias of the CUDA build for existing site pins.
if [[ "${GPU_VARIANT}" == "cuda" && "${IMAGE}" == *":1.70-mojo-cuda" ]]; then
  docker tag "${IMAGE}" "goliath/methylgrapher:1.70-mojo" || true
  log "also tagged goliath/methylgrapher:1.70-mojo -> ${IMAGE}"
fi

log "smoke"
bash "${DOCKER_DIR}/smoke_64k.sh" "${IMAGE}"

# Same image, Apptainer file. Docker stays the build format.
BUILD_SIF="${MOJO_ALIGN_BUILD_SIF:-auto}"
case "${BUILD_SIF}" in
  auto|require|skip) ;;
  *)
    echo "ERROR: MOJO_ALIGN_BUILD_SIF must be auto, require, or skip (got ${BUILD_SIF})" >&2
    exit 1
    ;;
esac
SIF_BUILDER=""
if command -v apptainer >/dev/null 2>&1; then
  SIF_BUILDER="apptainer"
elif command -v singularity >/dev/null 2>&1; then
  SIF_BUILDER="singularity"
fi
if [[ "${BUILD_SIF}" == "skip" ]]; then
  log "sif skipped (MOJO_ALIGN_BUILD_SIF=skip)"
elif [[ -z "${SIF_BUILDER}" ]]; then
  if [[ "${BUILD_SIF}" == "require" ]]; then
    echo "ERROR: MOJO_ALIGN_BUILD_SIF=require but apptainer/singularity is not on PATH" >&2
    exit 1
  fi
  log "sif skipped (apptainer not installed; MOJO_ALIGN_BUILD_SIF=require to fail instead)"
else
  if [[ -n "${MOJO_ALIGN_SIF:-}" ]]; then
    SIF="${MOJO_ALIGN_SIF}"
  elif [[ -d /work/goliath/images ]]; then
    SIF="/work/goliath/images/methylgrapher-${IMAGE_TAG}.sif"
  else
    SIF="${DOCKER_DIR}/methylgrapher-${IMAGE_TAG}.sif"
  fi
  mkdir -p "$(dirname "${SIF}")"
  SIF_PARTIAL="${SIF}.partial"
  rm -f "${SIF_PARTIAL}"
  log "packing ${SIF} from docker-daemon://${IMAGE}"
  "${SIF_BUILDER}" build "${SIF_PARTIAL}" "docker-daemon://${IMAGE}"
  mv -f "${SIF_PARTIAL}" "${SIF}"
  log "sif smoke: ${SIF}"
  if ! bash "${SCRIPT_DIR}/run_mojo_align_sif.sh" "${SIF}" help >/tmp/mg_sif_help.txt 2>/tmp/mg_sif_help.err; then
    if ! METHYLGRAPHER_MCALL_ENGINE=python \
      bash "${SCRIPT_DIR}/run_mojo_align_sif.sh" "${SIF}" help >/tmp/mg_sif_help.txt 2>/tmp/mg_sif_help.err; then
      cat /tmp/mg_sif_help.err >&2
      echo "ERROR: sif smoke failed for ${SIF}" >&2
      exit 1
    fi
  fi
  grep -E 'Align|MethylCall|MergeCpG' /tmp/mg_sif_help.txt
  log "sif ready: ${SIF}"
  log "run: bash ${SCRIPT_DIR}/run_mojo_align_sif.sh ${SIF} --work /work -- Align ..."
fi

log "done: ${IMAGE}"
log "Pin via actionConfig.methylgrapher_wgbs.engine=mojo image=${IMAGE}"
log "ROCm twin: MOJO_ALIGN_GPU_VARIANT=rocm MOJO_ALIGN_IMAGE_TAG=1.70-mojo-rocm $0"

# Fleet NFS publish: writes tar + BOTH pin files (config Id and OCI manifest Id).
# Sisters on containerd report the OCI manifest digest as docker image Id; forgetting
# .image_id.oci is what made enable_fleet_mojo_align.sh refuse a good load.
if [[ "${METHYL_PUBLISH_FLEET_IMAGE:-0}" == "1" ]]; then
  PUBLISH_IMAGE="${IMAGE}"
  if [[ "${GPU_VARIANT}" == "cuda" ]]; then
    PUBLISH_IMAGE="${METHYL_MOJO_ALIGN_IMAGE:-${METHYL_METHYLGRAPHER_MOJO_IMAGE:-goliath/methylgrapher:1.70-mojo}}"
  fi
  log "METHYL_PUBLISH_FLEET_IMAGE=1 → publishing ${PUBLISH_IMAGE} to NFS"
  METHYL_MOJO_ALIGN_IMAGE="${PUBLISH_IMAGE}" \
    bash "${SCRIPT_DIR}/publish_mojo_align_fleet_image.sh"
else
  log "fleet publish skipped (set METHYL_PUBLISH_FLEET_IMAGE=1 to write NFS tar + .image_id[.oci])"
fi
# Leave staged engine for inspect; CI may clean.
