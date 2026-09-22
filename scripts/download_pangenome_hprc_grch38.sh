#!/usr/bin/env bash
# Download HPRC v1.1 MC GRCh38 AF-filtered pangenome graph and build
# Parabricks giraffe short-read indexes for MethylPipeline sample prep.
#
# Produces files matching tools/methyl-config-editor/configs/site_grch38.example.json
# under PANGENOME_DIR (default /work/genomes/pangenome/GRCh38/d9/1.70).
#
# WGBS CAVEAT: the stock HPRC d9.gbz is NOT bisulfite-converted. For production
# WGBS, replace GBZ with your operator-built C->T graph and re-run the index steps
# (set PANGENOME_GBZ to that file, or export SKIP_DOWNLOAD=1).
#
# Requirements: aws CLI (or curl), docker, ~200GB+ free disk (autoindex temp space)
#
# Usage:
#   sudo mkdir -p /work/genomes/pangenome/GRCh38/d9/1.70
#   sudo chown "$USER" /work/genomes/pangenome/GRCh38/d9/1.70
#   scripts/download_pangenome_hprc_grch38.sh
#
# Optional env:
#   PANGENOME_DIR=/work/genomes/pangenome/GRCh38/d9/1.70
#   VG_IMAGE=quay.io/vgteam/vg:v1.70.0
#   SKIP_DOWNLOAD=1          # only (re)build indexes from existing GBZ
#   PANGENOME_GBZ=/path/to/custom.bs.gbz
#   FORCE=1                  # re-run autoindex / ref_paths even if outputs exist

set -euo pipefail

PANGENOME_DIR="${PANGENOME_DIR:-/work/genomes/pangenome/GRCh38/d9/1.70}"
VG_IMAGE="${VG_IMAGE:-quay.io/vgteam/vg:v1.70.0}"
VG_VERSION_TAG="${VG_VERSION_TAG:-1.70}"
PREFIX="hprc-v1.1-mc-grch38.d9"
AUTOINDEX_PREFIX="${PREFIX}.autoindex.${VG_VERSION_TAG}"

S3_BASE="s3://human-pangenomics/pangenomes/freeze/freeze1/minigraph-cactus/hprc-v1.1-mc-grch38"
HTTPS_BASE="https://s3-us-west-2.amazonaws.com/human-pangenomics/pangenomes/freeze/freeze1/minigraph-cactus/hprc-v1.1-mc-grch38"

usage() {
  cat <<'EOF'
Usage: scripts/download_pangenome_hprc_grch38.sh

Download HPRC GRCh38 d9.gbz and build vg autoindex + ref_paths for Parabricks giraffe.

Environment:
  PANGENOME_DIR     Output directory (default: /work/genomes/pangenome/GRCh38/d9/1.70)
  VG_IMAGE          vg Docker image (default: quay.io/vgteam/vg:v1.70.0)
  PANGENOME_GBZ     Override GBZ path (for custom WGBS graphs)
  SKIP_DOWNLOAD=1   Skip S3/curl download; index from existing GBZ only
  FORCE=1           Rebuild autoindex and ref_paths even when present

See docs/implementation/sample-preparation-flow.md (pangenome alignment section).
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

mkdir -p "${PANGENOME_DIR}"
cd "${PANGENOME_DIR}"

GBZ="${PANGENOME_GBZ:-${PANGENOME_DIR}/${PREFIX}.gbz}"
DIST="${PANGENOME_DIR}/${AUTOINDEX_PREFIX}.dist"
MIN="${PANGENOME_DIR}/${AUTOINDEX_PREFIX}.shortread.withzip.min"
ZIP="${PANGENOME_DIR}/${AUTOINDEX_PREFIX}.shortread.zipcodes"
PATHS_SUB="${PANGENOME_DIR}/${PREFIX}.paths.sub"

log() { printf '[INFO] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

download_gbz() {
  if [[ -f "${GBZ}" ]]; then
    log "GBZ already present: ${GBZ}"
    return 0
  fi
  log "Downloading ${PREFIX}.gbz (large; may take a while)..."
  if command -v aws >/dev/null 2>&1; then
    aws s3 cp "${S3_BASE}/${PREFIX}.gbz" "${GBZ}" --no-sign-request
  else
    curl -fL --retry 5 --continue-at - \
      "${HTTPS_BASE}/${PREFIX}.gbz" -o "${GBZ}"
  fi
}

run_vg() {
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -v "${PANGENOME_DIR}:/workdir" \
    -w /workdir \
    "${VG_IMAGE}" \
    vg "$@"
}

preflight_vg() {
  # The vgteam/vg images bundle jemalloc compiled for 4 KB memory pages.
  # On 64 KB-page kernels (e.g. NVIDIA Grace / GH200, aarch64 *-64k) jemalloc
  # aborts at startup ("Unsupported system page size", SIGSEGV). Detect that
  # here and print actionable guidance instead of the cryptic crash.
  local out rc=0
  out="$(run_vg version 2>&1)" || rc=$?
  if [[ ${rc} -eq 0 ]]; then
    return 0
  fi

  local page_size cause
  page_size="$(getconf PAGE_SIZE 2>/dev/null || echo unknown)"
  if printf '%s' "${out}" | grep -qi 'Unsupported system page size'; then
    cause="bundled jemalloc was compiled for 4096-byte pages and aborts on
                   64 KB-page kernels (NVIDIA Grace / GH200, *-64k). No runtime
                   override exists (page size is compile-time)."
  elif printf '%s' "${out}" | grep -qi 'exec format error'; then
    cause="image architecture does not match this host and cannot execute
                   (no working emulation)."
  else
    cause="see the container error below."
  fi

  cat >&2 <<EOF
[ERROR] ${VG_IMAGE} cannot run on this host.

  Host           : $(uname -m), $(uname -r), page size ${page_size} bytes
  Cause          : ${cause}

  Container error:
    $(printf '%s' "${out}" | sed 's/^/    /')

  The 'vg autoindex' step is a one-time OFFLINE prep and its outputs
  (.dist / .shortread.withzip.min / .shortread.zipcodes / .paths.sub) are
  portable data files, not executables. Build them on a standard 4 KB-page
  x86_64 host, then copy them into ${PANGENOME_DIR} here.

  On a 4 KB-page host with Docker, using the SAME vg version
  (v1.70.0 -> Parabricks-compatible 'autoindex.1.70' format):

    PANGENOME_DIR=/tmp/pg SKIP_DOWNLOAD=1 \\
      scripts/download_pangenome_hprc_grch38.sh   # after copying the .gbz there

  Then copy the built indexes back to this host:

    scp builder:/tmp/pg/${AUTOINDEX_PREFIX}.dist \\
        builder:/tmp/pg/${AUTOINDEX_PREFIX}.shortread.withzip.min \\
        builder:/tmp/pg/${AUTOINDEX_PREFIX}.shortread.zipcodes \\
        builder:/tmp/pg/${PREFIX}.paths.sub \\
        ${PANGENOME_DIR}/

  'pbrun giraffe' (NVIDIA Parabricks image) supports Grace/GH200 and runs on
  this host; only the CPU vg index-prep container has this limitation.
EOF
  exit 2
}

build_autoindex() {
  local gbz_basename
  gbz_basename="$(basename "${GBZ}")"
  if [[ "${FORCE:-0}" != "1" && -f "${DIST}" && -f "${MIN}" && -f "${ZIP}" ]]; then
    log "Autoindex files already present; skipping vg autoindex"
    return 0
  fi
  log "Running vg autoindex (needs substantial CPU/RAM/disk)..."
  run_vg autoindex \
    -p "${AUTOINDEX_PREFIX}" \
    -G "${gbz_basename}" \
    -w giraffe
}

build_ref_paths() {
  local gbz_basename paths_raw
  gbz_basename="$(basename "${GBZ}")"
  paths_raw="${PANGENOME_DIR}/${PREFIX}.paths"
  if [[ "${FORCE:-0}" != "1" && -f "${PATHS_SUB}" ]]; then
    log "ref_paths already present: ${PATHS_SUB}"
    return 0
  fi
  log "Extracting GRCh38 reference paths for surjection..."
  run_vg paths -x "${gbz_basename}" -L --paths-by GRCh38 > "${paths_raw}"
  grep -v _decoy "${paths_raw}" \
    | grep -v _random \
    | grep -v chrUn_ \
    | grep -v chrEBV \
    | grep -v chrM \
    | grep -v chain_ > "${PATHS_SUB}"
  rm -f "${paths_raw}"
}

verify_outputs() {
  local missing=0 f
  for f in "${GBZ}" "${DIST}" "${MIN}" "${ZIP}" "${PATHS_SUB}"; do
    if [[ ! -f "${f}" ]]; then
      log "MISSING: ${f}"
      missing=1
    fi
  done
  if [[ "${missing}" -ne 0 ]]; then
    die "One or more outputs missing under ${PANGENOME_DIR}"
  fi
  log "All pangenome index files ready under ${PANGENOME_DIR}"
  cat <<EOF

Add to /work/site/methyl_site.json (adjust linear_ref_fasta to your site FASTA):

  "pangenome_genome": {
    "gbz": "${GBZ}",
    "dist": "${DIST}",
    "min": "${MIN}",
    "zipcodes": "${ZIP}",
    "ref_paths": "${PATHS_SUB}",
    "linear_ref_fasta": "/work/genomes/linear/GRCh38/ensembl-116/Homo_sapiens.GRCh38.dna.primary_assembly.fa"
  }

Enable pangenome alignment in profile/context:
  "actionConfig": { "parabricks": { "alignment_mode": "pangenome" } }

WGBS: if using a custom bisulfite graph, set PANGENOME_GBZ to that .gbz and re-run
with SKIP_DOWNLOAD=1 after placing the file, or FORCE=1 to rebuild indexes.
EOF
}

main() {
  if [[ "${SKIP_DOWNLOAD:-0}" != "1" ]]; then
    download_gbz
  elif [[ ! -f "${GBZ}" ]]; then
    die "SKIP_DOWNLOAD=1 but GBZ not found: ${GBZ}"
  fi

  local need_index=0
  if [[ "${FORCE:-0}" == "1" ]]; then
    need_index=1
  elif [[ ! -f "${DIST}" || ! -f "${MIN}" || ! -f "${ZIP}" || ! -f "${PATHS_SUB}" ]]; then
    need_index=1
  fi
  if [[ "${need_index}" -eq 1 ]]; then
    preflight_vg
  fi

  build_autoindex
  build_ref_paths
  verify_outputs
}

main "$@"
