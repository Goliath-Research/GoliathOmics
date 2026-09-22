#!/usr/bin/env bash
# Ensure the methylGrapher worker image is present on this node.
# Deploy path only — never compiles vg.
#
# Resolution order:
#   1. Image already present locally
#   2. METHYLGRAPHER_IMAGE_TAR → docker load (CI artifact / release bundle)
#   3. docker pull ${METHYL_METHYLGRAPHER_IMAGE} (when hosted in a registry)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/detect_platform.sh"

IMAGE="${METHYL_METHYLGRAPHER_IMAGE:-$(resolve_methylgrapher_image 2>/dev/null || true)}"
TAR="${METHYLGRAPHER_IMAGE_TAR:-}"

log() { printf '[ensure-methylgrapher] %s\n' "$*"; }
die() { printf '[ensure-methylgrapher] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -n "${IMAGE}" ]] || die "METHYL_METHYLGRAPHER_IMAGE unset and platform_matrix has no default"

if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  log "already present: ${IMAGE}"
  exit 0
fi

if [[ -n "${TAR}" && -f "${TAR}" ]]; then
  log "docker load < ${TAR}"
  gzip -dc "${TAR}" 2>/dev/null | docker load || docker load <"${TAR}"
  docker image inspect "${IMAGE}" >/dev/null 2>&1 \
    || die "loaded archive but image ${IMAGE} not found (retag needed?)"
  log "loaded ${IMAGE}"
  exit 0
fi

log "docker pull ${IMAGE}"
docker pull "${IMAGE}"
log "pulled ${IMAGE}"
