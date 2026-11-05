#!/bin/bash
# Package GoliathExtractor binary + HDF5 plugin for production release.
# Run from GoliathExtractor repo root after 'make', or pass --build-dir.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/package_goliath_extractor.sh [options]

Options:
  --build-dir PATH     GoliathExtractor build root (default: . or GOLIATH_EXTRACTOR_ROOT)
  --arch KEY           arm64|x64|aarch64|amd64 (default: detect)
  --version VER        Version string for tarball name (default: git describe or manual)
  --output PATH        Output tarball path
  -h, --help           Show this help

Output layout inside tarball:
  bin/goliath-extractor (legacy tarballs may ship bin/goliath-extractor)
  lib/hdf5_zstd_plugin/
  lib/libhts.so.3, libdeflate.so.0, libhtscodecs.so.2 (bundled; RUNPATH $ORIGIN/../lib)
  VERSION.txt

Reference for GoliathExtractor repo CI: GoliathExtractor/.github/workflows/release.yml
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=goliath_extractor_env_compat.sh
source "$SCRIPT_DIR/goliath_extractor_env_compat.sh"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

BUILD_DIR="${GOLIATH_EXTRACTOR_ROOT:-${GOLIATH_EXTRACTOR_ROOT:-.}}"
if [[ -z "${GOLIATH_EXTRACTOR_ROOT:-}" && -n "${GOLIATH_EXTRACTOR_ROOT:-}" ]]; then
  _goliath_extractor_warn_deprecated_env "GOLIATH_EXTRACTOR_ROOT" "GOLIATH_EXTRACTOR_ROOT"
fi
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

BIN="$BUILD_DIR/build/dynamic/$ME_SUBDIR/goliath-extractor"
LEGACY_BIN="$BUILD_DIR/build/dynamic/$ME_SUBDIR/MethylExtractor"
PLUGIN_SRC="$BUILD_DIR/build/dynamic/$ME_SUBDIR/hdf5_zstd_plugin"
if [[ ! -x "$BIN" ]]; then
  BIN="$LEGACY_BIN"
fi
[[ -x "$BIN" ]] || die "GoliathExtractor binary not found under $BUILD_DIR/build/dynamic/$ME_SUBDIR (run make first)"

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

OUTPUT="${OUTPUT:-goliath-extractor-linux-${ARCH_KEY}.tar.gz}"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

mkdir -p "$STAGING/bin" "$STAGING/lib"
cp -a "$BIN" "$STAGING/bin/$(basename "$BIN")"
if [[ "$(basename "$BIN")" == MethylExtractor ]]; then
  ln -sf MethylExtractor "$STAGING/bin/goliath-extractor"
elif [[ ! -f "$STAGING/bin/goliath-extractor" ]]; then
  cp -a "$BIN" "$STAGING/bin/goliath-extractor"
fi
if [[ -d "$PLUGIN_SRC" ]]; then
  cp -a "$PLUGIN_SRC" "$STAGING/lib/hdf5_zstd_plugin"
fi
echo "$VERSION" >"$STAGING/VERSION.txt"

cat >"$STAGING/env.sh" <<'ENVEOF'
#!/bin/sh
# Source after extract: source "$(dirname "$0")/env.sh"  (from install root)
_ROOT="$(cd "$(dirname "$0")" && pwd)"
export GOLIATH_EXTRACTOR_BIN="$_ROOT/bin/goliath-extractor"
export METHYL_EXTRACTOR_BIN="$_ROOT/bin/goliath-extractor"
export HDF5_PLUGIN_PATH="$_ROOT/lib/hdf5_zstd_plugin"
export LD_LIBRARY_PATH="$_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$_ROOT/bin:$PATH"
ENVEOF
chmod +x "$STAGING/env.sh"

tar -czf "$OUTPUT" -C "$STAGING" bin lib VERSION.txt env.sh
# Ship libhts.so.3 (+ libdeflate, libhtscodecs) in lib/ with RUNPATH $ORIGIN/../lib; the
# worker image does not have them. Set GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR to copy
# from a local dir instead of the Ubuntu .debs.
bundle_args=(--tarball "$OUTPUT" --arch "$ARCH_KEY")
if [[ -n "${GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR:-}" ]]; then
  bundle_args+=(--lib-dir "$GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR")
elif [[ -n "${GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR:-}" ]]; then
  _goliath_extractor_warn_deprecated_env "GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR" "GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR"
  bundle_args+=(--lib-dir "$GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR")
fi
bash "$SCRIPT_DIR/bundle_goliath_extractor_libs.sh" "${bundle_args[@]}"
SHA="$(sha256sum "$OUTPUT" | awk '{print $1}')"
info "Wrote $OUTPUT (sha256=$SHA)"
info "Add to manifest.json artifacts.$ARCH_KEY.sha256"
