#!/usr/bin/env bash
# 64K-page ARM64 smoke for the methylGrapher worker image (python or mojo).
set -euo pipefail

IMAGE="${1:-${METHYL_METHYLGRAPHER_IMAGE:-goliath/methylgrapher:1.70}}"

echo "== host page size =="
getconf PAGE_SIZE || true

echo "== image: ${IMAGE} =="
docker image inspect "${IMAGE}" >/dev/null

echo "== methylGrapher help =="
# Mojo CUDA JIT needs a visible GPU for DeviceContext kernels. Prefer --gpus
# on mojo-cuda images; fall back to Python-engine help when no GPU in CI.
DOCKER_GPU=()
if [[ "${IMAGE}" == *mojo* ]] && docker info 2>/dev/null | grep -qi nvidia; then
  DOCKER_GPU=(--gpus all)
fi
if ! docker run --rm "${DOCKER_GPU[@]}" "${IMAGE}" methylGrapher help >/tmp/mg_help.txt 2>/tmp/mg_help.err; then
  if ! docker run --rm -e METHYLGRAPHER_MCALL_ENGINE=python "${IMAGE}" methylGrapher help >/tmp/mg_help.txt 2>/tmp/mg_help.err; then
    docker run --rm "${IMAGE}" methylGrapher --help >/tmp/mg_help.txt
  fi
fi
grep -E 'Align|MethylCall|MergeCpG' /tmp/mg_help.txt

if [[ "${IMAGE}" == *mojo* ]]; then
  echo "== mojo engine provenance =="
  docker run --rm "${IMAGE}" python3 -c "import sys; sys.path.insert(0,'/opt/mojo-align'); import engine; print(engine.__file__)"
  if [[ ${#DOCKER_GPU[@]} -gt 0 ]]; then
    echo "== Mojo DeviceContext CUDA probe =="
    docker run --rm "${DOCKER_GPU[@]}" \
      -e MODULAR_CACHE_DIR=/tmp/modular_cache \
      -e MODULAR_NVPTX_COMPILER_PATH=/opt/mojo-align/cuda/bin/ptxas \
      -w /opt/mojo-align \
      "${IMAGE}" bash -lc '
        set -euo pipefail
        python3 -c "import cupy; print(\"cupy\", cupy.__version__, \"devices\", cupy.cuda.runtime.getDeviceCount())"
        # Use the same Mojo runtime env as methylGrapher.mojo.sh (LD_PRELOAD python).
        export MODULAR_HOME=/opt/mojo-align/mojo-env/share/max
        export LD_LIBRARY_PATH=/opt/mojo-align/mojo-env/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
        export LD_PRELOAD=/opt/mojo-align/mojo-env/lib/libpython3.13.so.1.0${LD_PRELOAD:+:$LD_PRELOAD}
        export PYTHONHOME=/opt/mojo-align/mojo-env
        export PATH=/opt/mojo-align/mojo-env/bin:$PATH
        mojo -I src tests/probe_devicecontext_cuda.mojo
      '
  fi
fi

echo "== vg version =="
docker run --rm "${IMAGE}" vg version

echo "== samtools =="
docker run --rm "${IMAGE}" samtools --version | head -n 2

echo "OK: methylGrapher image smoke passed for ${IMAGE}"
