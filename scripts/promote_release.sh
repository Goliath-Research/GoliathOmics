#!/bin/bash
# Promote a release on shared storage: extract MethylExtractor, install venv, docker pull, flip current.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/promote_release.sh [options]

Options:
  --root PATH           GoliathOmics root (default: /work/goliath)
  --release DIR         Release directory (default: <root>/releases/<version> or --version)
  --version VER         Release version (alternative to --release)
  --arch KEY            aarch64 or amd64 (default: detect)
  --pull-parabricks     docker pull into shared data-root (once per release)
  --skip-docker-pull    Do not pull Parabricks
  --skip-venv           Skip venv install
  --skip-extractor      Skip MethylExtractor extract
  --recreate-venv       Pass through to install_release.sh
  --worker-api-base URL WORKER_API_BASE for worker.env
  -h, --help            Show this help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
RELEASE_DIR=""
VERSION=""
ARCH=""
PULL_PB=0
SKIP_PULL=0
SKIP_VENV=0
SKIP_EXTRACTOR=0
RECREATE_VENV=0
WORKER_API_BASE="${WORKER_API_BASE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --release) RELEASE_DIR="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --pull-parabricks) PULL_PB=1; shift ;;
    --skip-docker-pull) SKIP_PULL=1; shift ;;
    --skip-venv) SKIP_VENV=1; shift ;;
    --skip-extractor) SKIP_EXTRACTOR=1; shift ;;
    --recreate-venv) RECREATE_VENV=1; shift ;;
    --worker-api-base) WORKER_API_BASE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
if [[ -n "$VERSION" ]]; then
  VERSION="$(normalize_release_version "$VERSION")"
  require_release_version "$VERSION" "--version" || exit 1
fi
if [[ -n "$VERSION" && -z "$RELEASE_DIR" ]]; then
  RELEASE_DIR="$ROOT/releases/$VERSION"
fi
[[ -d "$RELEASE_DIR" ]] || die "Release directory not found: $RELEASE_DIR"
MANIFEST="$RELEASE_DIR/manifest.json"
[[ -f "$MANIFEST" ]] || die "Missing manifest: $MANIFEST"

MANIFEST_VERSION="$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('version',''))")"
[[ -n "$MANIFEST_VERSION" ]] || die "manifest.json missing version"
require_release_version "$MANIFEST_VERSION" "manifest version" || exit 1

TARBALL_NAME="$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m['artifacts']['$ARCH']['methyl_extractor'])")"
EXPECTED_SHA="$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('artifacts',{}).get('$ARCH',{}).get('sha256',''))")"
TARBALL_PATH="$RELEASE_DIR/$TARBALL_NAME"
DOCKER_ROOT="$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('docker_data_root','/work/goliath/docker'))")"
PARABRICKS_IMAGE="$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('parabricks_image',''))")"
RELEASE_VERSION="$(python3 -c "import json; m=json.load(open('$MANIFEST')); print(m.get('version',''))")"

verify_checksum() {
  [[ -n "$EXPECTED_SHA" ]] || { info "No sha256 in manifest; skipping checksum"; return 0; }
  local actual
  actual="$(sha256sum "$TARBALL_PATH" | awk '{print $1}')"
  [[ "$actual" == "$EXPECTED_SHA" ]] || die "Checksum mismatch for $TARBALL_NAME (expected $EXPECTED_SHA, got $actual)"
  info "Checksum OK: $TARBALL_NAME"
}

install_extractor() {
  [[ "$SKIP_EXTRACTOR" -eq 1 ]] && return 0
  [[ -f "$TARBALL_PATH" ]] || die "MethylExtractor tarball not found: $TARBALL_PATH"
  verify_checksum
  local dest="$ROOT/methyl-extractor-${ARCH}"
  info "Extracting MethylExtractor to $dest"
  rm -rf "$dest"
  mkdir -p "$dest"
  tar -xzf "$TARBALL_PATH" -C "$dest"
  [[ -x "$dest/bin/MethylExtractor" ]] || die "Expected $dest/bin/MethylExtractor after extract"
  chmod -R a-w "$dest" 2>/dev/null || true
  info "MethylExtractor installed: $dest/bin/MethylExtractor"
}

install_venv() {
  [[ "$SKIP_VENV" -eq 1 ]] && return 0
  local install_script="$RELEASE_DIR/runtime-bundle/scripts/install_release.sh"
  [[ -x "$install_script" ]] || install_script="$SCRIPT_DIR/install_release.sh"
  local args=(--release-dir "$RELEASE_DIR" --venv "$ROOT/venv-${ARCH}")
  [[ "$RECREATE_VENV" -eq 1 ]] && args+=(--recreate-venv)
  bash "$install_script" "${args[@]}"
}

pull_parabricks() {
  [[ "$SKIP_PULL" -eq 1 ]] && return 0
  [[ "$PULL_PB" -eq 1 ]] || return 0
  [[ -n "$PARABRICKS_IMAGE" ]] || die "parabricks_image missing from manifest"
  local gpu_script="$RELEASE_DIR/runtime-bundle/scripts/setup_gpu_node.sh"
  [[ -x "$gpu_script" ]] || gpu_script="$SCRIPT_DIR/setup_gpu_node.sh"
  export METHYL_PARABRICKS_IMAGE="$PARABRICKS_IMAGE"
  bash "$gpu_script" \
    --docker-data-root "${DOCKER_ROOT:-/work/goliath/docker}" \
    --env-dir "$ROOT/env" \
    --pull-parabricks \
    --skip-docker
}

flip_current() {
  local link="$ROOT/current"
  ln -sfn "$RELEASE_DIR" "$link"
  info "Updated $link -> $RELEASE_DIR"
}

write_env() {
  # Prefer the script next to this promote. Target deploys run the checkout
  # copy, which merges existing worker.env keys; the release bundle may not.
  local env_script="$SCRIPT_DIR/write_worker_env.sh"
  if [[ ! -x "$env_script" ]]; then
    env_script="$RELEASE_DIR/runtime-bundle/scripts/write_worker_env.sh"
  fi
  local args=(--root "$ROOT" --manifest "$MANIFEST" --arch "$ARCH")
  [[ -n "$WORKER_API_BASE" ]] && args+=(--worker-api-base "$WORKER_API_BASE")
  bash "$env_script" "${args[@]}"
}

rollouts_begin goliathomics-runtime "promote ${ARCH} $(hostname -s 2>/dev/null || hostname)"

install_extractor
install_venv
pull_parabricks
flip_current
write_env

rollouts_set_message "promoted $(basename "$RELEASE_DIR") arch=${ARCH} current=$ROOT/current"
info "Promoted release ${RELEASE_VERSION:-unknown} for arch=$ARCH"
