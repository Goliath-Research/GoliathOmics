#!/bin/bash
# Package MethylExtractor binary + HDF5 plugin for production release.
# Run from MethylExtractor repo root after 'make', or pass --build-dir.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/package_methyl_extractor.sh [options]

Options:
  --build-dir PATH     MethylExtractor build root (default: . or METHYL_EXTRACTOR_ROOT)
  --arch KEY           arm64|x64|aarch64|amd64 (default: detect)
  --version VER        Version string for tarball name (default: git describe or manual)
  --output PATH        Output tarball path
  -h, --help           Show this help

Output layout inside tarball:
  bin/MethylExtractor
  lib/hdf5_zstd_plugin/
  VERSION.txt

Reference for MethylExtractor repo CI: MethylExtractor/.github/workflows/release.yml
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

BUILD_DIR="${METHYL_EXTRACTOR_ROOT:-.}"
ARCH=""
VERSION=""
OUTPUT=""
VERSION_EXPLICIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-dir) BUILD_DIR="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; VERSION_EXPLICIT=1; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

die() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

normalize_arch() {
  case "$1" in
    aarch64|arm64) echo "aarch64" ;;
    x86_64|amd64|x64) echo "amd64" ;;
    *) die "Unknown arch: $1" ;;
  esac
}

normalize_me_subdir() {
  case "$1" in
    aarch64) echo "arm64" ;;
    amd64) echo "x64" ;;
    *) die "Unknown arch: $1" ;;
  esac
}

ARCH_KEY="$(normalize_arch "${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}")"
ME_SUBDIR="$(normalize_me_subdir "$ARCH_KEY")"
BUILD_DIR="$(cd "$BUILD_DIR" && pwd)"

BIN="$BUILD_DIR/build/dynamic/$ME_SUBDIR/MethylExtractor"
PLUGIN_SRC="$BUILD_DIR/build/dynamic/$ME_SUBDIR/hdf5_zstd_plugin"
[[ -x "$BIN" ]] || die "MethylExtractor binary not found: $BIN (run make first)"

if [[ -z "$VERSION" ]]; then
  if git -C "$BUILD_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    VERSION="$(git -C "$BUILD_DIR" describe --tags --always 2>/dev/null || echo manual)"
    VERSION="$(normalize_release_version "$VERSION")"
  else
    VERSION="manual"
  fi
fi

if [[ "$VERSION_EXPLICIT" -eq 1 ]]; then
  require_release_version "$VERSION" "--version" || exit 1
fi

OUTPUT="${OUTPUT:-methyl-extractor-linux-${ARCH_KEY}.tar.gz}"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

mkdir -p "$STAGING/bin" "$STAGING/lib"
cp -a "$BIN" "$STAGING/bin/MethylExtractor"
if [[ -d "$PLUGIN_SRC" ]]; then
  cp -a "$PLUGIN_SRC" "$STAGING/lib/hdf5_zstd_plugin"
fi
echo "$VERSION" >"$STAGING/VERSION.txt"

cat >"$STAGING/env.sh" <<'ENVEOF'
#!/bin/sh
# Source after extract: source "$(dirname "$0")/env.sh"  (from install root)
_ROOT="$(cd "$(dirname "$0")" && pwd)"
export METHYL_EXTRACTOR_BIN="$_ROOT/bin/MethylExtractor"
export HDF5_PLUGIN_PATH="$_ROOT/lib/hdf5_zstd_plugin"
export PATH="$_ROOT/bin:$PATH"
ENVEOF
chmod +x "$STAGING/env.sh"

tar -czf "$OUTPUT" -C "$STAGING" bin lib VERSION.txt env.sh
SHA="$(sha256sum "$OUTPUT" | awk '{print $1}')"
info "Wrote $OUTPUT (sha256=$SHA)"
info "Add to manifest.json artifacts.$ARCH_KEY.sha256"