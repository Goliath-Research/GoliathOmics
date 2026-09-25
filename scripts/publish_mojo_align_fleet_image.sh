#!/usr/bin/env bash
# Atomically publish goliath/methylgrapher:1.70-mojo to NFS for the GH200 fleet.
#
# Pin files (stale .image_id.oci is what broke sisters before):
#   methylgrapher-1.70-mojo.image_id      — docker config digest (classic graph driver)
#   methylgrapher-1.70-mojo.image_id.oci  — OCI image manifest digest (containerd hosts)
# When the tar is legacy docker-archive (no OCI index), .image_id.oci is removed so a
# previous publish's digest cannot pass enable_fleet / verify against the new tar.
#
# Usage (on 50-58 after build):
#   bash scripts/publish_mojo_align_fleet_image.sh
#   METHYL_MOJO_ALIGN_IMAGE=goliath/methylgrapher:1.70-mojo \
#     bash scripts/publish_mojo_align_fleet_image.sh
#
# Optional:
#   METHYL_FLEET_IMAGES_DIR=/work/goliath/images
#   METHYL_CLEAR_SISTER_READY=1   # default 1 — drop sister .ready so reload runs
set -euo pipefail

IMAGE="${METHYL_MOJO_ALIGN_IMAGE:-}"
if [[ -z "${IMAGE}" && -n "${METHYL_METHYLGRAPHER_MOJO_IMAGE:-}" ]]; then
  echo "warning: METHYL_METHYLGRAPHER_MOJO_IMAGE is deprecated; use METHYL_MOJO_ALIGN_IMAGE" >&2
  IMAGE="${METHYL_METHYLGRAPHER_MOJO_IMAGE}"
fi
IMAGE="${IMAGE:-goliath/methylgrapher:1.70-mojo}"
IMG_DIR="${METHYL_FLEET_IMAGES_DIR:-/work/goliath/images}"
TAR="${IMG_DIR}/methylgrapher-1.70-mojo.tar"
ID_FILE="${IMG_DIR}/methylgrapher-1.70-mojo.image_id"
OCI_FILE="${ID_FILE}.oci"
RELOAD_MARKER="${IMG_DIR}/fleet-reload-requested"
READY_DIR="${IMG_DIR}/fleet-ready"
CLEAR_SISTER_READY="${METHYL_CLEAR_SISTER_READY:-1}"

log() { echo "[publish-mojo-fleet] $*"; }

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  log "ERROR: image not local: ${IMAGE}" >&2
  exit 1
fi

CONFIG_ID="$(docker image inspect -f '{{.Id}}' "${IMAGE}")"
if [[ -z "${CONFIG_ID}" || "${CONFIG_ID}" != sha256:* ]]; then
  log "ERROR: unexpected config id: ${CONFIG_ID}" >&2
  exit 1
fi

mkdir -p "${IMG_DIR}"
STAGE="${TAR}.staging.$$"
trap 'rm -f "${STAGE}"' EXIT

log "saving ${IMAGE} -> ${STAGE}"
docker save "${IMAGE}" -o "${STAGE}"

# Prefer OCI index manifest digest (what containerd/docker-with-containerd reports as Id).
OCI_ID="$(
  python3 - "${STAGE}" <<'PY'
import json, sys, tarfile
path = sys.argv[1]
with tarfile.open(path) as tf:
    names = set(tf.getnames())
    if "index.json" in names:
        idx = json.load(tf.extractfile("index.json"))
        for m in idx.get("manifests") or []:
            # Prefer the image manifest, not an attestation/index child.
            mt = m.get("mediaType") or ""
            dig = m.get("digest") or ""
            if dig.startswith("sha256:") and "image.manifest" in mt:
                print(dig)
                raise SystemExit(0)
        # Fallback: first sha256 digest in index.
        for m in idx.get("manifests") or []:
            dig = m.get("digest") or ""
            if dig.startswith("sha256:"):
                print(dig)
                raise SystemExit(0)
    # Legacy docker-archive: no OCI manifest digest — caller must drop .image_id.oci.
    if "manifest.json" in names:
        raise SystemExit(0)
raise SystemExit("no OCI index.json in tar")
PY
)"

if [[ -z "${OCI_ID}" ]]; then
  log "WARN: tar has no OCI index manifest digest; removing stale ${OCI_FILE} if present"
else
  log "oci_manifest=${OCI_ID}"
fi
log "config_id=${CONFIG_ID}"

# Atomic replace so a concurrent sister load never sees a truncated tar.
mv -f "${STAGE}" "${TAR}"
trap - EXIT
chmod 600 "${TAR}" || true

printf '%s\n' "${CONFIG_ID}" > "${ID_FILE}"
if [[ -n "${OCI_ID}" ]]; then
  printf '%s\n' "${OCI_ID}" > "${OCI_FILE}"
else
  # Must not leave a prior publish's OCI pin — containerd sisters would accept it
  # against a tar that no longer contains that manifest.
  rm -f "${OCI_FILE}"
fi

date -u +%Y-%m-%dT%H:%M:%SZ > "${RELOAD_MARKER}"

if [[ "${CLEAR_SISTER_READY}" == "1" ]]; then
  mkdir -p "${READY_DIR}"
  for h in 192-222-51-118 192-222-51-168 192-222-57-3; do
    rm -f "${READY_DIR}/${h}.ready"
  done
  log "cleared sister .ready stamps (50-58 left intact)"
fi

log "published ${TAR}"
log "pin config: ${ID_FILE} -> ${CONFIG_ID}"
if [[ -n "${OCI_ID}" ]]; then
  log "pin oci:    ${OCI_FILE} -> ${OCI_ID}"
else
  log "pin oci:    removed (legacy docker-archive; no manifest digest)"
fi
log "reload marker: ${RELOAD_MARKER}"
log "sisters: bash /work/goliath/images/sister_reload_mojo_align.sh"
log "then on 50-58: python3 /work/goliath/images/restore_wgbs_capabilities.py"
