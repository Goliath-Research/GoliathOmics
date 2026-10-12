#!/usr/bin/env bash
# Create the per-arch worker virtualenv from a release tree (wheels + lockfile).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_release.sh [options]

Options:
  --root PATH       Goliath root (default: /work/goliath)
  --release PATH    Release directory (manifest, wheels/, requirements-worker.lock)
  --arch KEY        aarch64 or amd64 (default: detect)
  --python BIN      Python used to create the venv (default: python3)
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
RELEASE=""
ARCH=""
PYTHON_BIN="${PYTHON_BIN:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --release) RELEASE="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --python) PYTHON_BIN="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$RELEASE" ]] || die "--release is required"
[[ -d "$RELEASE" ]] || die "Release directory not found: $RELEASE"
ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
[[ "$ARCH" == "aarch64" || "$ARCH" == "amd64" ]] || die "Unknown arch: $ARCH"

VENV="$ROOT/venv-$ARCH"
LOCK=""
for cand in "$RELEASE/requirements-worker.lock" "$RELEASE/runtime-bundle/requirements-worker.lock"; do
  if [[ -f "$cand" ]]; then
    LOCK="$cand"
    break
  fi
done
WHEELS=""
for cand in "$RELEASE/wheels" "$RELEASE/runtime-bundle/wheels"; do
  if [[ -d "$cand" ]]; then
    WHEELS="$cand"
    break
  fi
done

info "Installing release venv $VENV"
mkdir -p "$ROOT"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
if [[ -n "$LOCK" ]]; then
  find_links=()
  if [[ -n "$WHEELS" ]]; then
    find_links=(--find-links "$WHEELS")
  fi
  "$VENV/bin/python" -m pip install -U pip
  "$VENV/bin/python" -m pip install -r "$LOCK" "${find_links[@]}"
else
  info "No requirements-worker.lock under $RELEASE; venv left without a lock install"
fi
info "Release venv ready: $VENV"
