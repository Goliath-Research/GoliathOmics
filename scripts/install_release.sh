#!/bin/bash
# Install production release wheels into a shared venv (non-editable).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_release.sh [options]

Options:
  --release-dir PATH   Release directory with wheels/ and requirements-worker.lock
  --venv PATH          Target virtualenv (default: /work/goliath/venv-<arch>)
  --python BIN         Python interpreter for venv creation (default: python3.12)
  --index-url URL      Optional PyPI index (GitHub Packages pypi-goliath)
  --recreate-venv      Remove existing venv before install
  -h, --help           Show this help

Examples:
  scripts/install_release.sh --release-dir /work/goliath/releases/2026.6.1 --venv /work/goliath/venv-aarch64
  scripts/install_release.sh --release-dir /work/goliath/current --index-url "https://pypi.pkg.github.com/Goliath-Research/simple/"
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

RELEASE_DIR=""
VENV_DIR=""
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
INDEX_URL=""
RECREATE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-dir) RELEASE_DIR="${2:-}"; shift 2 ;;
    --venv) VENV_DIR="${2:-}"; shift 2 ;;
    --python) PYTHON_BIN="${2:-}"; shift 2 ;;
    --index-url) INDEX_URL="${2:-}"; shift 2 ;;
    --recreate-venv) RECREATE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

ARCH_KEY="$(platform_arch_key "$(detect_uname_arch)")"
RELEASE_DIR="${RELEASE_DIR:-/work/goliath/current}"
VENV_DIR="${VENV_DIR:-/work/goliath/venv-${ARCH_KEY}}"

[[ -d "$RELEASE_DIR" ]] || die "Release directory not found: $RELEASE_DIR"

LOCK_FILE="$RELEASE_DIR/requirements-worker.lock"
if [[ ! -f "$LOCK_FILE" ]]; then
  LOCK_FILE="$RELEASE_DIR/requirements-worker.txt"
fi
[[ -f "$LOCK_FILE" ]] || die "Missing requirements lock: $RELEASE_DIR/requirements-worker.lock"

WHEELS_DIR="$RELEASE_DIR/wheels"
if [[ -z "$INDEX_URL" && ! -d "$WHEELS_DIR" ]]; then
  die "Missing wheels directory: $WHEELS_DIR (or pass --index-url)"
fi

if [[ "$RECREATE" -eq 1 && -d "$VENV_DIR" ]]; then
  info "Removing existing venv: $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  info "Creating venv: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
pip install -U pip wheel setuptools

PIP_ARGS=(-r "$LOCK_FILE")
if [[ -n "$INDEX_URL" ]]; then
  PIP_ARGS=(--index-url "$INDEX_URL" "${PIP_ARGS[@]}")
else
  PIP_ARGS=(--no-index --find-links "$WHEELS_DIR" "${PIP_ARGS[@]}")
fi

info "Installing from $LOCK_FILE into $VENV_DIR"
if [[ -z "$INDEX_URL" ]]; then
  # Local wheels cover goliath packages only; third-party deps need PyPI or an index mirror.
  pip install --find-links "$WHEELS_DIR" "${PIP_ARGS[@]}"
else
  pip install "${PIP_ARGS[@]}"
fi

info "Release install complete: $VENV_DIR"
