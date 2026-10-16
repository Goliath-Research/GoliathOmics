#!/bin/bash
# Install production release wheels into a pod-local venv (non-editable).
# The venv must not live on a shared volume (/work ignores exec bits).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_release.sh [options]

Options:
  --release-dir PATH   Release directory with wheels/ and requirements-worker.lock
  --venv PATH          Target virtualenv on local disk
                       (default: ${GOLIATH_LOCAL_ROOT:-/opt/goliath}/venv-<arch>; never under GOLIATH_WORK_MOUNT)
  --python BIN         Python interpreter for venv creation (default: python3.12)
  --index-url URL      Also allow this package index (e.g. https://pypi.org/simple).
                       Omit to install offline from wheels/ in the release directory only.
                       Either way the lock pins are honored (hashes enforced when present).
  --recreate-venv      Remove existing venv before install
  -h, --help           Show this help

Examples:
  scripts/install_release.sh --release-dir /work/goliath/releases/2026.6.1 --venv /opt/goliath/venv-aarch64
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=worker_venv_local.sh
source "$SCRIPT_DIR/worker_venv_local.sh"

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
VENV_DIR="${VENV_DIR:-$(worker_venv_local_dir "$ARCH_KEY")}"
# A venv on the shared volume cannot run: exec bits are ignored, and
# ensurepip fails on the first file it writes and reads back.
if _wvl_on_work "$VENV_DIR"; then
  die "venv ${VENV_DIR} is on the shared work mount ${GOLIATH_WORK_MOUNT:-/work}; use pod-local disk (GOLIATH_LOCAL_ROOT)"
fi

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
  mkdir -p "$(dirname "$VENV_DIR")"
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    rm -rf "$VENV_DIR"
    command -v uv >/dev/null 2>&1 || die "$PYTHON_BIN -m venv $VENV_DIR failed (is the python venv/ensurepip package installed?)"
    info "$PYTHON_BIN -m venv failed; retrying with uv venv --seed"
    uv venv --seed --python "$PYTHON_BIN" "$VENV_DIR" || die "could not create venv $VENV_DIR"
  fi
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
if [[ "${GOLIATH_VENV_UPGRADE_PIP:-1}" == "1" ]]; then
  pip install -U pip wheel setuptools
fi

# Older locks embedded the build host's --find-links (a GitHub runner path
# such as /home/runner/work/_temp/release/wheels). Install from a copy with
# every find-links/index option removed so only our own wheels dir is used.
CLEAN_LOCK="$(mktemp "${TMPDIR:-/tmp}/goliath-lock.XXXXXX")"
trap 'rm -f "$CLEAN_LOCK"' EXIT
sed -E '/^[[:space:]]*(-f|--find-links|-i|--index-url|--extra-index-url|--trusted-host|--no-index)([[:space:]=]|$)/d' \
  "$LOCK_FILE" >"$CLEAN_LOCK"

PIP_ARGS=()
if [[ -n "$INDEX_URL" ]]; then
  PIP_ARGS+=(--index-url "$INDEX_URL")
else
  PIP_ARGS+=(--no-index)
fi
# The local wheels dir is the only find-links location, listed once.
if [[ -d "$WHEELS_DIR" ]]; then
  PIP_ARGS+=(--find-links "$WHEELS_DIR")
fi
if grep -q -- '--hash=' "$CLEAN_LOCK"; then
  PIP_ARGS+=(--require-hashes)
fi
PIP_ARGS+=(-r "$CLEAN_LOCK")

if [[ -n "$INDEX_URL" ]]; then
  info "Installing from $LOCK_FILE into $VENV_DIR (wheels/ + index $INDEX_URL)"
else
  info "Installing from $LOCK_FILE into $VENV_DIR (offline, wheels/ only)"
fi
pip install "${PIP_ARGS[@]}"

info "Release install complete: $VENV_DIR"
