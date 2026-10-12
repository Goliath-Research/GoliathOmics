#!/usr/bin/env bash
# First-worker / joiner bootstrap invoked by provision_worker_node.sh.
# Database DDL is not applied here.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap_goliath.sh [options]

Options:
  --root PATH
  --arch KEY
  --release-dir PATH
  --gpu
  --require-arc          Warn when verify_arc_prereqs.sh is missing; run it when present
  --promote-release      promote_release.sh (includes a Parabricks pull unless skipped)
  --skip-parabricks-pull
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
ARCH=""
RELEASE_DIR=""
GPU=0
REQUIRE_ARC=0
PROMOTE=0
SKIP_PULL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --release-dir) RELEASE_DIR="${2:-}"; shift 2 ;;
    --gpu) GPU=1; shift ;;
    --require-arc) REQUIRE_ARC=1; shift ;;
    --promote-release) PROMOTE=1; shift ;;
    --skip-parabricks-pull) SKIP_PULL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
RELEASE_DIR="${RELEASE_DIR:-$ROOT/current}"

if [[ "$PROMOTE" -eq 1 ]]; then
  [[ -d "$RELEASE_DIR" ]] || die "Release directory not found: $RELEASE_DIR"
  pull_flag=(--skip-docker-pull)
  if [[ "$SKIP_PULL" -eq 0 && "$GPU" -eq 1 ]]; then
    pull_flag=(--pull-parabricks)
  fi
  bash "$SCRIPT_DIR/promote_release.sh" \
    --root "$ROOT" --release "$RELEASE_DIR" --arch "$ARCH" "${pull_flag[@]}"
else
  gpu_args=(--root "$ROOT" --arch "$ARCH" --skip-docker-pull)
  [[ "$GPU" -eq 1 ]] && gpu_args+=(--require-gpu)
  bash "$SCRIPT_DIR/setup_gpu_node.sh" "${gpu_args[@]}"
  if [[ -d "$RELEASE_DIR" ]]; then
    bash "$SCRIPT_DIR/install_release.sh" --root "$ROOT" --release "$RELEASE_DIR" --arch "$ARCH"
  fi
  bash "$SCRIPT_DIR/write_worker_env.sh" --root "$ROOT" --arch "$ARCH"
fi

if [[ "$REQUIRE_ARC" -eq 1 ]]; then
  if [[ -f "$SCRIPT_DIR/verify_arc_prereqs.sh" ]]; then
    bash "$SCRIPT_DIR/verify_arc_prereqs.sh"
  else
    info "verify_arc_prereqs.sh is not in this tree; Arc check skipped"
  fi
fi

info "bootstrap_goliath complete arch=$ARCH"
