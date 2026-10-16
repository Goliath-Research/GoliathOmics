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

# Extract a tarball into DEST without asking DEST's filesystem to preserve
# owner, mode or mtime. Shared volumes (a Runpod global network volume,
# root-squashed NFS) reject chown/chmod/utime and GNU tar turns each rejection
# into exit 2. Extract into a local staging dir (never under the work mount),
# copy with plain cp -R (no -p/-a), then set modes warn-and-continue:
# dirs 0755 / files 0644 (2775 / 0664 with GOLIATH_SHARED_GROUP), executables
# stay executable (0755 / 0775). Corrupt or truncated archives still fail.
safe_extract_tarball() {
  local tarball="$1"
  local dest="$2"
  local work_mount="${GOLIATH_WORK_MOUNT:-/work}"
  local stage_base="${GOLIATH_STAGE_DIR:-${TMPDIR:-/tmp}}"
  local stage dmode fmode xmode path failed=0 group="${GOLIATH_SHARED_GROUP:-}"
  local shared=0
  if [[ -n "$group" ]] && getent group "$group" >/dev/null 2>&1; then
    shared=1
  fi
  case "$stage_base" in
    "${work_mount%/}"|"${work_mount%/}"/*) stage_base="/tmp" ;;
  esac
  mkdir -p "$stage_base"
  stage="$(mktemp -d "${stage_base%/}/goliath-extract.XXXXXX")"
  if ! tar --no-same-owner --no-same-permissions -m -xzf "$tarball" -C "$stage"; then
    rm -rf "$stage"
    die "could not extract ${tarball} (corrupt or truncated archive?)"
  fi
  mkdir -p "$dest"
  if ! cp -R "$stage/." "$dest/"; then
    rm -rf "$stage"
    die "could not copy ${tarball} contents into ${dest}"
  fi
  rm -rf "$stage"
  if [[ "$shared" -eq 1 ]]; then
    dmode=2775; fmode=0664; xmode=0775
  else
    dmode=0755; fmode=0644; xmode=0755
  fi
  while IFS= read -r -d '' path; do
    chmod "$dmode" "$path" 2>/dev/null || failed=$((failed + 1))
    if [[ "$shared" -eq 1 ]]; then chgrp "$group" "$path" 2>/dev/null || failed=$((failed + 1)); fi
  done < <(find "$dest" -type d -print0)
  while IFS= read -r -d '' path; do
    if [[ -x "$path" ]]; then
      chmod "$xmode" "$path" 2>/dev/null || failed=$((failed + 1))
    else
      chmod "$fmode" "$path" 2>/dev/null || failed=$((failed + 1))
    fi
    if [[ "$shared" -eq 1 ]]; then chgrp "$group" "$path" 2>/dev/null || failed=$((failed + 1)); fi
  done < <(find "$dest" -type f -print0)
  if [[ "$failed" -gt 0 ]]; then
    echo "[WARN] ${failed} chmod/chgrp operations under ${dest} were not applied (filesystem rejects them); continuing" >&2
  fi
}

install_extractor() {
  [[ "$SKIP_EXTRACTOR" -eq 1 ]] && return 0
  [[ -f "$TARBALL_PATH" ]] || die "MethylExtractor tarball not found: $TARBALL_PATH"
  verify_checksum
  local dest="$ROOT/methyl-extractor-${ARCH}"
  info "Extracting MethylExtractor to $dest"
  rm -rf "$dest"
  mkdir -p "$dest"
  safe_extract_tarball "$TARBALL_PATH" "$dest"
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
  local marker="$ROOT/current.ready"
  local tmp="$ROOT/.current.ready.$$"
  # ln -sfn replaces the symlink by renaming a temporary link in this directory.
  # Some shared volumes do not support atomic rename (a Runpod global volume
  # is one). Readers must wait for current.ready, written after the symlink
  # update returns.
  ln -sfn "$RELEASE_DIR" "$link"
  printf '%s\n' "$RELEASE_DIR" >"$tmp"
  mv -f "$tmp" "$marker"
  info "Updated $link -> $RELEASE_DIR (commit marker $marker)"
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
