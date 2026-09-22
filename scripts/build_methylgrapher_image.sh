#!/usr/bin/env bash
# Build the methylGrapher(+vg) worker image once (CI / image refresh).
#
# Deployments must NOT run this. They only pull/load the published image
# (see scripts/ensure_methylgrapher_image.sh).
#
# Arm64: compiles vg with jemalloc=off so the binary runs on 64 KB-page
# kernels (Grace/GH200). Amd64: uses the stock vg GitHub release binary.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKER_DIR="${REPO_ROOT}/workers/docker/methylgrapher"

VG_VERSION="${VG_VERSION:-1.70.0}"
# Image tag is independent of vg semver (1.70.0 compile pin → publish :1.70).
# Must match scripts/platform_matrix.env METHYLGRAPHER_IMAGE_*.
IMAGE_TAG="${METHYLGRAPHER_IMAGE_TAG:-1.70}"
METHYLGRAPHER_VERSION="${METHYLGRAPHER_VERSION:-0.2.0}"
IMAGE="${METHYL_METHYLGRAPHER_IMAGE:-goliath/methylgrapher:${IMAGE_TAG}}"
VG_SRC_DIR="${VG_SRC_DIR:-${REPO_ROOT}/.cache/vg-src/vg}"
SAVE_TAR="${METHYLGRAPHER_IMAGE_TAR:-}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
INSTALL_DEPS="${INSTALL_DEPS:-1}"

log() { printf '[methylgrapher-image] %s\n' "$*"; }
die() { printf '[methylgrapher-image] ERROR: %s\n' "$*" >&2; exit 1; }

arch="$(uname -m)"
case "$arch" in
  aarch64|arm64) arch_key=arm64 ;;
  x86_64|amd64) arch_key=amd64 ;;
  *) die "unsupported arch: $arch" ;;
esac

# Only enough to clone vg and invoke its own get-deps target.
install_bootstrap_deps() {
  [[ "${INSTALL_DEPS}" == "1" ]] || return 0
  if ! command -v sudo >/dev/null 2>&1; then
    log "sudo unavailable; assuming build deps already installed"
    return 0
  fi
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    build-essential make git ca-certificates curl
}

# vg owns its compile dependency list (Dockerfile DEPS markers), so get-deps
# always matches v${VG_VERSION}. Duplicating it here silently omitted bison/flex
# and cairo, which fails late in the build at lib/libraptor2.a.
# Runs with CWD inside ${VG_SRC_DIR}.
install_vg_deps() {
  [[ "${INSTALL_DEPS}" == "1" ]] || return 0
  command -v sudo >/dev/null 2>&1 || return 0
  log "installing vg build deps via make get-deps (pinned to v${VG_VERSION})"
  make get-deps DEBIAN_FRONTEND=noninteractive
}

build_vg_arm64() {
  mkdir -p "$(dirname "${VG_SRC_DIR}")"
  if [[ ! -d "${VG_SRC_DIR}/.git" ]]; then
    git clone --branch "v${VG_VERSION}" https://github.com/vgteam/vg.git "${VG_SRC_DIR}"
  fi
  (
    cd "${VG_SRC_DIR}"
    git fetch --tags --force
    git checkout "v${VG_VERSION}"
    local i
    for i in 1 2 3 4 5 6 7 8; do
      git submodule sync --recursive
      if git submodule update --init --recursive; then
        break
      fi
      log "submodule update failed (attempt ${i}); retrying..."
      sleep $((i * 3))
      if [[ "${i}" -eq 8 ]]; then
        die "git submodule update failed after retries (often sourceware.org/elfutils 502)"
      fi
    done
    install_vg_deps
    make -j"$(nproc)" jemalloc=off
  )
  [[ -x "${VG_SRC_DIR}/bin/vg" ]] || die "vg binary missing after build"
  "${VG_SRC_DIR}/bin/vg" version
  cp -f "${VG_SRC_DIR}/bin/vg" "${DOCKER_DIR}/vg.arm64"
  chmod +x "${DOCKER_DIR}/vg.arm64"
  strip_staged "${DOCKER_DIR}/vg.arm64"
  stage_vg_libs
}

# vg compiles with -ggdb -g, so the binary is ~600 MB of mostly debug symbols.
# Strip the build-context copy only; ${VG_SRC_DIR} keeps its symbols.
strip_staged() {
  command -v strip >/dev/null 2>&1 || { log "strip unavailable; shipping unstripped $1"; return 0; }
  local before after
  before="$(stat -c %s "$1")"
  strip --strip-unneeded "$1"
  after="$(stat -c %s "$1")"
  log "stripped $(basename "$1"): ${before} -> ${after} bytes"
}

# Ship the vg-built shared objects the binary resolves at load time. Whatever ldd
# reports missing inside the image has to land here or vg exits 127.
stage_vg_libs() {
  local dest="${DOCKER_DIR}/vg_libs"
  rm -rf "${dest}"
  mkdir -p "${dest}"
  local lib
  for lib in libhandlegraph.so; do
    [[ -f "${VG_SRC_DIR}/lib/${lib}" ]] || die "missing ${VG_SRC_DIR}/lib/${lib}"
    cp -f "${VG_SRC_DIR}/lib/${lib}" "${dest}/"
    strip_staged "${dest}/${lib}"
  done
  log "staged $(ls "${dest}" | tr '\n' ' ')"
}

# The Dockerfile COPYs vg_libs/ unconditionally, so the dir must exist even when
# the stock release binary needs nothing from it.
stage_empty_vg_libs() {
  local dest="${DOCKER_DIR}/vg_libs"
  rm -rf "${dest}"
  mkdir -p "${dest}"
  : >"${dest}/.keep"
}

fetch_vg_amd64() {
  local dest="${DOCKER_DIR}/vg.amd64"
  curl -fsSL -o "${dest}" \
    "https://github.com/vgteam/vg/releases/download/v${VG_VERSION}/vg" \
  || curl -fsSL -o "${dest}" \
    "https://github.com/vgteam/vg/releases/download/v${VG_VERSION}/vg-amd64"
  chmod +x "${dest}"
  "${dest}" version
  stage_empty_vg_libs
}

build_docker() {
  local prebuilt="vg.${arch_key}"
  [[ -f "${DOCKER_DIR}/${prebuilt}" ]] || die "missing ${DOCKER_DIR}/${prebuilt}"
  # A stray bind mount (docker run -v .../Dockerfile:...) turns this into a
  # directory; the daemon then fails deep inside the build with a tmp path.
  if [[ -d "${DOCKER_DIR}/Dockerfile" ]]; then
    die "${DOCKER_DIR}/Dockerfile is a directory; remove it and restore the file (git checkout -- ${DOCKER_DIR#"${REPO_ROOT}/"}/Dockerfile)"
  fi
  [[ -f "${DOCKER_DIR}/Dockerfile" ]] || die "missing ${DOCKER_DIR}/Dockerfile"
  [[ -d "${DOCKER_DIR}/vg_libs" ]] || die "missing ${DOCKER_DIR}/vg_libs (staged by the vg build step)"
  docker build \
    --build-arg "VG_VERSION=${VG_VERSION}" \
    --build-arg "METHYLGRAPHER_VERSION=${METHYLGRAPHER_VERSION}" \
    --build-arg "VG_PREBUILT=${prebuilt}" \
    -t "${IMAGE}" \
    -f "${DOCKER_DIR}/Dockerfile" \
    "${DOCKER_DIR}"
  log "built ${IMAGE}"
}

maybe_smoke() {
  if [[ "${SKIP_SMOKE}" == "1" ]]; then
    log "SKIP_SMOKE=1; not running smoke_64k.sh"
    return 0
  fi
  # smoke_64k.sh exists to prove vg survives a 64 KB-page kernel, which only a
  # 65536-page host can show. 4K index-build hosts (amd64) defer to a 64K worker.
  local page
  page="$(getconf PAGE_SIZE 2>/dev/null || echo 0)"
  if [[ "${page}" != "65536" ]]; then
    log "host page size ${page} (not 65536); 64K smoke deferred to a 64K ARM64 worker"
    return 0
  fi
  bash "${DOCKER_DIR}/smoke_64k.sh" "${IMAGE}"
}

maybe_save() {
  [[ -n "${SAVE_TAR}" ]] || return 0
  mkdir -p "$(dirname "${SAVE_TAR}")"
  docker save "${IMAGE}" | gzip -c >"${SAVE_TAR}"
  log "saved ${SAVE_TAR} ($(du -h "${SAVE_TAR}" | awk '{print $1}'))"
}

main() {
  command -v docker >/dev/null 2>&1 || die "docker required"
  if [[ "${arch_key}" == "arm64" ]]; then
    install_bootstrap_deps
    build_vg_arm64
  else
    # Stock release binary; nothing is compiled, so no build deps are needed.
    fetch_vg_amd64
  fi
  build_docker
  maybe_smoke
  maybe_save
  log "OK — publish/push this image; workers only pull/load it"
}

main "$@"
