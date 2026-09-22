#!/bin/bash
# Download MethylExtractor tarballs from GitHub Releases into a release directory.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/download_methyl_extractor_artifacts.sh [options]

Options:
  --release-dir PATH     Target release directory (default: /work/goliath/releases/<version>)
  --version VER          GitHub Release tag version (SemVer, e.g. 2026.6.1; required)
  --organization URL     GitHub org URL or slug (default: https://github.com/Goliath-Research)
  --project NAME         GitHub org slug if organization is a URL (default: Goliath-Research)
  --repo SLUG            Owner/name of MethylExtractor repo (default: <org>/MethylExtractor)
  --feed NAME            Unused; kept for assemble_release.sh compatibility
  --arch KEY             Download one arch only (aarch64 or amd64); default both
  --install              Extract tarball and configure PATH + HDF5_PLUGIN_PATH
  --goliath-root PATH    Root for install (default: /work/goliath)
  -h, --help             Show this help

Requires: GitHub CLI (`gh`) authenticated with `contents:read` on MethylExtractor
(GH_TOKEN or `gh auth login`).

Example:
  scripts/download_methyl_extractor_artifacts.sh \
    --version 2026.6.1 \
    --release-dir /work/goliath/releases/2026.6.1 \
    --arch aarch64 --install
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

github_owner() {
  local s="$1"
  s="${s#https://github.com/}"
  s="${s#http://github.com/}"
  s="${s%/}"
  echo "${s%%/*}"
}

RELEASE_DIR=""
VERSION=""
ORG="${GITHUB_ORG:-https://github.com/Goliath-Research}"
PROJECT="${GITHUB_PROJECT:-Goliath-Research}"
FEED="${METHYL_EXTRACTOR_FEED:-methyl-extractor}"
REPO="${METHYL_EXTRACTOR_REPO:-}"
ARCH_FILTER=""
DO_INSTALL=0
GOLIATH_ROOT="${GOLIATH_ROOT:-/work/goliath}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --organization) ORG="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --feed) FEED="${2:-}"; shift 2 ;;
    --arch) ARCH_FILTER="${2:-}"; shift 2 ;;
    --install) DO_INSTALL=1; shift ;;
    --goliath-root) GOLIATH_ROOT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$VERSION" ]] || die "--version is required"
VERSION="$(normalize_release_version "$VERSION")"
require_release_version "$VERSION" "--version" || exit 1

OWNER="$(github_owner "$ORG")"
if [[ -z "$OWNER" || "$OWNER" == "$ORG" ]]; then
  OWNER="$PROJECT"
fi
REPO="${REPO:-${OWNER}/MethylExtractor}"
TAG="v${VERSION}"

RELEASE_DIR="${RELEASE_DIR:-/work/goliath/releases/$VERSION}"
mkdir -p "$RELEASE_DIR"

command -v gh >/dev/null 2>&1 || die "gh CLI is required to download MethylExtractor GitHub Releases"

download_one() {
  local arch_key="$1"
  local pkg_name="methyl-extractor-linux-${arch_key}"
  local tarball="$RELEASE_DIR/${pkg_name}.tar.gz"

  info "Downloading $pkg_name from $REPO@$TAG"
  rm -f "$tarball"
  gh release download "$TAG" \
    --repo "$REPO" \
    --pattern "${pkg_name}.tar.gz" \
    --dir "$RELEASE_DIR" \
    --clobber
  [[ -f "$tarball" ]] || die "Expected $tarball after gh release download"
  info "Wrote $tarball ($(sha256sum "$tarball" | awk '{print $1}'))"
}

# --feed is accepted so assemble_release.sh can pass it; GitHub Releases do not use it.
: "${FEED}"

for arch in aarch64 amd64; do
  [[ -n "$ARCH_FILTER" && "$ARCH_FILTER" != "$arch" ]] && continue
  download_one "$arch"
done

info "Download complete: $RELEASE_DIR"

if [[ "$DO_INSTALL" -eq 1 ]]; then
  if [[ -z "$ARCH_FILTER" ]]; then
    ARCH_FILTER="$(platform_arch_key "$(detect_uname_arch)")"
  fi
  tb="$RELEASE_DIR/methyl-extractor-linux-${ARCH_FILTER}.tar.gz"
  [[ -f "$tb" ]] || die "Tarball not found for install: $tb"
  bash "$SCRIPT_DIR/install_methyl_extractor_tarball.sh" \
    --tarball "$tb" \
    --arch "$ARCH_FILTER" \
    --goliath-root "$GOLIATH_ROOT"
else
  info "Install: scripts/install_methyl_extractor_tarball.sh --tarball <path>"
  info "Or re-run with --install (and --arch if needed)"
  info "Full release: assemble manifest.json then promote_release.sh"
fi
