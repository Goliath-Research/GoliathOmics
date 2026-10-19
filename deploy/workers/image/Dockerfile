# syntax=docker/dockerfile:1.7
# goliath-worker: provider-neutral GPU worker with Clara Parabricks on PATH
# (no nested docker). Base is NGC Clara 4.7.0-1 (Ubuntu 22.04 / jammy).
#
# Build requires NGC_API_KEY (docker login nvcr.io). See
# .github/workflows/goliath-worker-image.yml and deploy/workers/image/README.md.
#
# Parabricks EULA forbids public redistribution of Clara — publish this image
# to a *private* GHCR package only.
#
# System tools: release builds ubuntu-22.04-amd64 offline apt bundles; pods
# install from /work via install_system_deps.sh. python3.12 is baked (deadsnakes)
# and also carried in the jammy bundle.
#
# GoliathAlign / Mojo: release-assemble ships
# goliath-align-host-linux-amd64-jammy.tar.gz; start_shared_worker installs it
# onto local disk every boot (goliath_align_local.sh). No manual staging.

ARG CLARA_IMAGE=nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1
FROM ${CLARA_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv python3.12-dev \
      python3-pip \
      openssh-server \
      samtools bedtools fastp pigz gzip acl tar coreutils \
      tini \
 && ln -sf /usr/bin/python3.12 /usr/local/bin/python3 \
 && ln -sf /usr/bin/python3.12 /usr/local/bin/python \
 && mkdir -p /var/run/sshd /run/sshd \
 && ssh-keygen -A \
 && rm -rf /var/lib/apt/lists/*

RUN command -v pbrun >/dev/null \
 && pbrun version >/tmp/pbrun-version.txt \
 && cat /tmp/pbrun-version.txt

COPY start.sh /start.sh
RUN chmod 0755 /start.sh

ENV GOLIATH_START_KEEPALIVE=/start.sh \
    METHYL_PARABRICKS_MODE=host \
    METHYL_METHYLGRAPHER_MODE=auto \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/start.sh"]
