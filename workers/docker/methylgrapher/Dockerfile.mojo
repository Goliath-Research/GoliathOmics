# mojo-align dual-ship image (native Mojo CLI + patched engine) for WGBS SamplePrep.
# Same vg/jemalloc=off story as Dockerfile; entrypoint stays `methylGrapher`.
#
# Build via scripts/build_mojo_align_image.sh (stages engine/, src/, and a
# trimmed mojo-env/ from MOJO_ALIGN_ROOT before docker build).
#
# Smoke:
#   bash workers/docker/methylgrapher/smoke_64k.sh goliath/methylgrapher:1.70-mojo
#
# Rollback: set actionConfig.methylgrapher_wgbs.engine=python and image
# goliath/methylgrapher:1.70. In-image: METHYLGRAPHER_MCALL_ENGINE=python
# (methylGrapher.mojo.sh skips Mojo and runs engine.cli).

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-pip python3-venv \
        samtools tabix pigz bwa \
        libcairo2 libatomic1 libgomp1 \
        libopenblas0 libgfortran5 libblas3 \
    && rm -rf /var/lib/apt/lists/* \
    && case "$(uname -m)" in \
         aarch64) libdir=aarch64-linux-gnu ;; \
         x86_64) libdir=x86_64-linux-gnu ;; \
         *) echo "unsupported arch $(uname -m)" >&2; exit 1 ;; \
       esac \
    && ln -sfn libopenblas.so.0 "/usr/lib/${libdir}/libcblas.so.3" \
    && ln -sfn libopenblas.so.0 "/usr/lib/${libdir}/libblas.so.3" \
    && ldconfig

# cuda | rocm — same userspace binary; host runtime + site image tag select the GPU.
ARG GPU_VARIANT=cuda
ENV METHYLGRAPHER_GPU_VARIANT=${GPU_VARIANT}

# Mojo 1.0 CUDA create on driver <580 needs system ptxas (staged by build script).
COPY cuda/bin/ptxas /opt/mojo-align/cuda/bin/ptxas
RUN chmod +x /opt/mojo-align/cuda/bin/ptxas \
    && ln -sf /opt/mojo-align/cuda/bin/ptxas /usr/local/bin/ptxas
ENV MODULAR_NVPTX_COMPILER_PATH=/opt/mojo-align/cuda/bin/ptxas

ARG VG_VERSION=1.70.0
ARG VG_PREBUILT=vg.arm64

COPY ${VG_PREBUILT} /usr/local/bin/vg
COPY vg_libs/ /usr/local/lib/
RUN chmod +x /usr/local/bin/vg && ldconfig && vg version

# Stock 0.2.0 kept as rollback reference inside the image (optional import).
ARG METHYLGRAPHER_VERSION=0.2.0
RUN pip3 install --no-cache-dir --break-system-packages \
      "methylGrapher==${METHYLGRAPHER_VERSION}" \
    || pip3 install --no-cache-dir --break-system-packages \
      "git+https://github.com/twlab/methylGrapher.git@v${METHYLGRAPHER_VERSION}"

# System CuPy (tooling / non-Mojo probes).
RUN if [ "${GPU_VARIANT}" = "cuda" ]; then \
      pip3 install --no-cache-dir --break-system-packages "cupy-cuda12x[ctk]>=13.0" \
        || pip3 install --no-cache-dir --break-system-packages "cupy-cuda12x[ctk]" \
        || echo "WARN: system cupy-cuda12x not installed"; \
    fi

# Patched engine + native Mojo CLI / MethylCall hot path + Giraffe GPU helper.
COPY engine /opt/mojo-align/engine
COPY src /opt/mojo-align/src
COPY scripts /opt/mojo-align/scripts
COPY tests /opt/mojo-align/tests
COPY mojo-env /opt/mojo-align/mojo-env
COPY methylGrapher.mojo.sh /usr/local/bin/methylGrapher
ENV METHYLGRAPHER_GPU_GIRAFFE_FALLBACK=mojo \
    METHYLGRAPHER_GIRAFFE_DEVICE=auto \
    METHYLGRAPHER_GPU_REQUIRE=1 \
    PYTHONPATH=/opt/mojo-align/scripts:/opt/mojo-align
# CuPy into mojo-env CPython 3.13 — Align quartet_map runs under PYTHONHOME.
# Trimmed pixi env has no pip; bootstrap via ensurepip / get-pip.
RUN if [ "${GPU_VARIANT}" = "cuda" ] && [ -x /opt/mojo-align/mojo-env/bin/python3 ]; then \
      /opt/mojo-align/mojo-env/bin/python3 -m ensurepip --upgrade \
        || curl -fsSL https://bootstrap.pypa.io/get-pip.py \
           | /opt/mojo-align/mojo-env/bin/python3 \
        || true; \
      /opt/mojo-align/mojo-env/bin/python3 -m pip install --no-cache-dir \
        "cupy-cuda12x[ctk]>=13.0" \
        || /opt/mojo-align/mojo-env/bin/python3 -m pip install --no-cache-dir \
          "cupy-cuda12x[ctk]" \
        || echo "WARN: mojo-env cupy not installed; GPU seed will host-fallback"; \
    fi
# Validate Python engine at build time. Mojo help/Align JIT needs a visible GPU
# arch (use smoke_64k.sh with --gpus); do not invoke Mojo here in the builder.
RUN chmod +x /usr/local/bin/methylGrapher /opt/mojo-align/mojo-env/bin/mojo \
    && python3 -c "import sys; sys.path.insert(0,'/opt/mojo-align'); from engine import cli; from engine.align_backends import normalize_align_engine; assert normalize_align_engine('mojo_giraffe')=='mojo_giraffe'; print('engine ok')" \
    && METHYLGRAPHER_MCALL_ENGINE=python methylGrapher help | head -5

LABEL org.opencontainers.image.title="mojo-align WGBS worker" \
      org.opencontainers.image.description="Native Mojo MethylCall + MojoGiraffe GAF + MojoFq2bamMeth + patched engine + vg for dual Align" \
      methylpipeline.vg_version="${VG_VERSION}" \
      methylpipeline.methylgrapher_engine="mojo" \
      methylpipeline.methylgrapher_version="0.1.0-mojo" \
      methylpipeline.giraffe="mojo_giraffe" \
      methylpipeline.gpu_variant="${GPU_VARIANT}"

ENTRYPOINT []
CMD ["methylGrapher", "help"]
