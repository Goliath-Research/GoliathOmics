#!/usr/bin/env bash
# Download the jammy GoliathAlign host runtime tarball into a release directory.
#
#   scripts/download_goliath_align_artifacts.sh --version VER --release-dir DIR
#
# Asset: goliath-align-host-linux-amd64-jammy.tar.gz from GoliathAlign GitHub Releases.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

RELEASE_DIR="" VERSION=""
ORG="${GITHUB_ORG:-https://github.com/Goliath-Research}"
REPO="${GOLIATH_ALIGN_REPO:-}"
usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --organization) ORG="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }
[[ -n "$VERSION" ]] || die "--version is required"
[[ -n "$RELEASE_DIR" ]] || die "--release-dir is required"
VERSION="$(normalize_release_version "$VERSION")"
require_release_version "$VERSION" "--version" || exit 1
mkdir -p "$RELEASE_DIR"
OWNER="${ORG#https://github.com/}"; OWNER="${OWNER#http://github.com/}"; OWNER="${OWNER%%/*}"
[[ -n "$OWNER" ]] || OWNER="Goliath-Research"
REPO="${REPO:-${OWNER}/GoliathAlign}"
ASSET="goliath-align-host-linux-amd64-jammy.tar.gz"
command -v gh >/dev/null || die "gh is required"
info "Downloading $ASSET from $REPO v${VERSION}"
gh release download "v${VERSION}" --repo "$REPO" --pattern "$ASSET" --dir "$RELEASE_DIR" --clobber
[[ -f "$RELEASE_DIR/$ASSET" ]] || die "download failed: $RELEASE_DIR/$ASSET"
info "OK $RELEASE_DIR/$ASSET"
