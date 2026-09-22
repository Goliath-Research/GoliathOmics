# Multi-arch methylGrapher + vg worker image for WGBS pangenome SamplePrep.
# Build indexes on a 4K-page host; this image is for align/extract on 64K ARM64 workers.
#
# Official GitHub arm64 vg binaries abort on 64 KB-page kernels (jemalloc).
# On Grace/GH200, build vg with jemalloc=off and place it beside this Dockerfile:
#
#   cp /work/cache/vg-src/vg/bin/vg workers/docker/methylgrapher/vg.arm64
#   bash workers/docker/methylgrapher/build_image.sh
#
# Smoke:
#   bash workers/docker/methylgrapher/smoke_64k.sh

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-pip python3-venv \
        samtools tabix pigz \
        libcairo2 libatomic1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ARG VG_VERSION=1.70.0
ARG TARGETARCH
ARG VG_PREBUILT=vg.arm64

COPY ${VG_PREBUILT} /usr/local/bin/vg
# A host-compiled vg links vg's own libhandlegraph.so dynamically; without it the
# binary dies with exit 127 (not a jemalloc abort). Empty for stock release binaries.
COPY vg_libs/ /usr/local/lib/
RUN chmod +x /usr/local/bin/vg && ldconfig && vg version

ARG METHYLGRAPHER_VERSION=0.2.0
RUN pip3 install --no-cache-dir --break-system-packages \
      "methylGrapher==${METHYLGRAPHER_VERSION}" \
    || pip3 install --no-cache-dir --break-system-packages \
      "git+https://github.com/twlab/methylGrapher.git@v${METHYLGRAPHER_VERSION}"

# vg 1.70 writes GAF header lines that methylGrapher's reader cannot parse.
COPY patch_gaf_header.py /usr/local/share/patch_gaf_header.py
RUN python3 /usr/local/share/patch_gaf_header.py

LABEL org.opencontainers.image.title="methylGrapher WGBS worker" \
      org.opencontainers.image.description="methylGrapher + vg for MethylPipeline pangenome_wgbs SamplePrep" \
      methylpipeline.vg_version="${VG_VERSION}" \
      methylpipeline.methylgrapher_version="${METHYLGRAPHER_VERSION}"

ENTRYPOINT []
CMD ["methylGrapher", "--help"]
