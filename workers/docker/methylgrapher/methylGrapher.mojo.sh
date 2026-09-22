#!/usr/bin/env bash
# Container entrypoint for the :1.70-mojo image.
#
# Prefer the native Mojo CLI (MethylCall hot path) when the staged Mojo
# runtime is present; otherwise fall back to the patched Python engine
# (same argv surface).
#
# In-image MethylCall / CLI rollback (no image tag swap):
#   METHYLGRAPHER_MCALL_ENGINE=python
# skips the Mojo binary entirely and runs engine.cli. This must be honored
# in the shell — not only inside src/main.mojo — so a broken Mojo runtime
# can still be bypassed.
set -euo pipefail

ROOT="/opt/mojo-align"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

_mcall_engine="$(printf '%s' "${METHYLGRAPHER_MCALL_ENGINE:-native}" | tr '[:upper:]' '[:lower:]')"
if [[ "${_mcall_engine}" == "python" ]]; then
  exec python3 -m engine.cli "$@"
fi

MOJO_BIN="${ROOT}/mojo-env/bin/mojo"
if [[ -x "${MOJO_BIN}" ]]; then
  export MODULAR_HOME="${ROOT}/mojo-env/share/max"
  export LD_LIBRARY_PATH="${ROOT}/mojo-env/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  export PATH="${ROOT}/mojo-env/bin:${PATH}"
  # Mojo NVIDIA DeviceContext compile needs driver ≥580 OR a ptxas path.
  # (App code does not call CUDA Runtime; index H2D is Mojo enqueue_copy.)
  # Image bake stages ptxas at /opt/mojo-align/cuda/bin/ptxas.
  if [[ -z "${MODULAR_NVPTX_COMPILER_PATH:-}" ]]; then
    for _ptx in \
      "${ROOT}/cuda/bin/ptxas" \
      /usr/local/cuda/bin/ptxas \
      /usr/lib/cuda/bin/ptxas \
      /usr/bin/ptxas
    do
      if [[ -x "${_ptx}" ]]; then
        export MODULAR_NVPTX_COMPILER_PATH="${_ptx}"
        break
      fi
    done
  fi
  # Writable Modular cache for non-root workers (kernel compile artifacts).
  export MODULAR_CACHE_DIR="${MODULAR_CACHE_DIR:-/tmp/modular_cache}"
  mkdir -p "${MODULAR_CACHE_DIR}" 2>/dev/null || true
  # Scripts on PYTHONPATH for CuPy/GPU minimizer seed (quartet_map).
  export PYTHONPATH="${ROOT}/scripts:${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
  # Mojo std.python needs pixi CPython symbols globally (dlopen alone misses
  # Py_Initialize in the trimmed runtime-bundle layout).
  if [[ -e "${ROOT}/mojo-env/lib/libpython3.13.so.1.0" ]]; then
    export LD_PRELOAD="${ROOT}/mojo-env/lib/libpython3.13.so.1.0${LD_PRELOAD:+:$LD_PRELOAD}"
    export PYTHONHOME="${ROOT}/mojo-env"
  fi
  cd "${ROOT}"
  exec "${MOJO_BIN}" src/main.mojo "$@"
fi

exec python3 -m engine.cli "$@"
