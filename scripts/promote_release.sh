#!/usr/bin/env bash
# Point <root>/current at a release, install that arch's venv, and verify.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/promote_release.sh [options]

Options:
  --root PATH            Goliath root (default: /work/goliath)
  --release PATH         Release directory to make current (required)
  --arch KEY             aarch64 or amd64 (required)
  --pull-parabricks      docker pull Parabricks for this arch
  --skip-docker-pull     Do not pull images (default)
  -h, --help

After current is repointed and verify_setup passes, a Rollouts deployment is
closed for service goliathomics-runtime. Set GOLIATH_ROLLOUTS_EXTERNAL=1 or
include goliathomics-runtime in GOLIATH_ROLLOUTS_SKIP_SERVICES when an outer
job already owns that deployment.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
RELEASE=""
ARCH=""
PULL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --release) RELEASE="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --pull-parabricks) PULL=1; shift ;;
    --skip-docker-pull) PULL=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$RELEASE" ]] || die "--release is required"
[[ -d "$RELEASE" ]] || die "Release directory not found: $RELEASE"
[[ -n "$ARCH" ]] || die "--arch is required"
RELEASE="$(readlink -f "$RELEASE" 2>/dev/null || echo "$RELEASE")"

rollouts_begin goliathomics-runtime "promote ${ARCH} $(hostname -s 2>/dev/null || hostname)"

info "Installing venv for $ARCH"
bash "$SCRIPT_DIR/install_release.sh" --root "$ROOT" --release "$RELEASE" --arch "$ARCH"

tarball="$RELEASE/methyl-extractor-linux-${ARCH}.tar.gz"
if [[ -f "$tarball" ]]; then
  info "Installing MethylExtractor tarball"
  bash "$SCRIPT_DIR/install_methyl_extractor_tarball.sh" \
    --tarball "$tarball" \
    --arch "$ARCH" \
    --goliath-root "$ROOT" \
    --no-user-env
fi

gpu_args=(--root "$ROOT" --arch "$ARCH")
if [[ "$PULL" -eq 1 ]]; then
  gpu_args+=(--pull)
else
  gpu_args+=(--skip-docker-pull)
fi
bash "$SCRIPT_DIR/setup_gpu_node.sh" "${gpu_args[@]}"
bash "$SCRIPT_DIR/write_worker_env.sh" --root "$ROOT" --arch "$ARCH"

mkdir -p "$ROOT"
ln -sfn "$RELEASE" "$ROOT/current"
info "current -> $RELEASE"

verify="$ROOT/current/runtime-bundle/scripts/verify_setup.sh"
if [[ ! -x "$verify" && ! -f "$verify" ]]; then
  verify="$SCRIPT_DIR/verify_setup.sh"
fi
bash "$verify" --root "$ROOT"
info "Promoted $(basename "$RELEASE") -> $ROOT/current"
rollouts_set_message "promoted $(basename "$RELEASE") arch=${ARCH} current=$ROOT/current"
