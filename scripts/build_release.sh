#!/bin/bash
# Build a production release: wheels, lockfile, runtime-bundle, manifest stub.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/build_release.sh [options]

Options:
  --version VER        Release version string (required; SemVer e.g. 2026.6.1)
  --output DIR         Output directory (default: /work/goliath/releases/<version>)
  --python BIN         Python for build/venv (default: python3.12)
  --skip-wheels        Only build runtime-bundle and manifest stub
  --with-gpu-reqs      Include GPU requirements in lockfile compile
  -h, --help           Show this help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

VERSION=""
OUTPUT=""
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
SKIP_WHEELS=0
WITH_GPU=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --python) PYTHON_BIN="${2:-}"; shift 2 ;;
    --skip-wheels) SKIP_WHEELS=1; shift ;;
    --with-gpu-reqs) WITH_GPU=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$VERSION" ]] || die "--version is required"
VERSION="$(normalize_release_version "$VERSION")"
require_release_version "$VERSION" "--version" || exit 1
OUTPUT="${OUTPUT:-/work/goliath/releases/$VERSION}"
WHEELS_DIR="$OUTPUT/wheels"
RUNTIME_DIR="$OUTPUT/runtime-bundle"
mkdir -p "$OUTPUT" "$WHEELS_DIR" "$RUNTIME_DIR"

PARABRICKS_IMAGE="${METHYL_PARABRICKS_IMAGE:-$(resolve_parabricks_image)}"
ARCH_KEY="$(platform_arch_key "$(detect_uname_arch)")"

_resolve_pkg_path() {
  local name="$1"
  local candidates=(
    "$REPO_ROOT/packages/$name"
    "$REPO_ROOT/$name"
    "$REPO_ROOT/workers"
  )
  local path
  for path in "${candidates[@]}"; do
    if [[ -d "$path" && ( -f "$path/pyproject.toml" || -f "$path/setup.py" ) ]]; then
      echo "$path"
      return 0
    fi
  done
  return 1
}

build_wheels() {
  info "Building wheels into $WHEELS_DIR"
  "$PYTHON_BIN" -m pip install -U pip build wheel
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(echo "$line" | xargs)"
    [[ -z "$line" ]] && continue
    pkg_path="$(_resolve_pkg_path "$line")" || die "Package not found for packages.list entry: $line"
    info "  wheel: $line ($pkg_path)"
    "$PYTHON_BIN" -m pip wheel "$pkg_path" -w "$WHEELS_DIR" --no-deps
  done < "$REPO_ROOT/scripts/packages.list"
}

write_lockfile() {
  local req_combined="$OUTPUT/requirements-worker.in"
  cat "$REPO_ROOT/requirements-pipeline.txt" >"$req_combined"
  if [[ "$WITH_GPU" -eq 1 && -f "$REPO_ROOT/requirements-gpu-cuda12.txt" ]]; then
    echo "" >>"$req_combined"
    cat "$REPO_ROOT/requirements-gpu-cuda12.txt" >>"$req_combined"
  fi
  info "Compiling requirements-worker.lock"
  if "$PYTHON_BIN" -m pip install -q pip-tools 2>/dev/null; then
    "$PYTHON_BIN" -m piptools compile "$req_combined" \
      -o "$OUTPUT/requirements-worker.lock" \
      --find-links "$WHEELS_DIR" \
      --allow-unsafe \
      --strip-extras \
      --generate-hashes 2>/dev/null || \
    "$PYTHON_BIN" -m piptools compile "$req_combined" \
      -o "$OUTPUT/requirements-worker.lock" \
      --find-links "$WHEELS_DIR"
  else
    warn_fallback_lock "$req_combined"
  fi
}

warn_fallback_lock() {
  local req_in="$1"
  info "pip-tools not available; writing requirements-worker.lock from wheels + base reqs"
  {
    cat "$req_in"
    echo ""
    echo "# Local goliath wheels (install with --find-links wheels/)"
    for whl in "$WHEELS_DIR"/*.whl; do
      [[ -f "$whl" ]] || continue
      base="$(basename "$whl" | sed 's/-[0-9].*//')"
      echo "${base} @ file://$(readlink -f "$whl")"
    done
  } >"$OUTPUT/requirements-worker.lock"
}

build_runtime_bundle() {
  info "Building runtime-bundle in $RUNTIME_DIR"
  # Shared /work NFS may reject cp/rsync -a permission preservation; copy content only.
  local rsync_safe=(rsync -rl --delete --no-perms --no-owner --no-group --no-times)
  for sub in schemas scripts deploy; do
    if [[ -d "$REPO_ROOT/$sub" ]]; then
      "${rsync_safe[@]}" "$REPO_ROOT/$sub/" "$RUNTIME_DIR/$sub/"
    fi
  done
  if [[ -d "$REPO_ROOT/workflow_engine/domain" ]]; then
    mkdir -p "$RUNTIME_DIR/domain"
    "${rsync_safe[@]}" "$REPO_ROOT/workflow_engine/domain/" "$RUNTIME_DIR/domain/"
  fi
  if [[ -f "$REPO_ROOT/requirements-pipeline.txt" ]]; then
    cp -f "$REPO_ROOT/requirements-pipeline.txt" "$RUNTIME_DIR/"
  fi
  if [[ -f "$REPO_ROOT/requirements-gpu-cuda12.txt" ]]; then
    cp -f "$REPO_ROOT/requirements-gpu-cuda12.txt" "$RUNTIME_DIR/"
  fi
  # Ensure detect_platform and platform_matrix travel with scripts
  cp -f "$REPO_ROOT/scripts/detect_platform.sh" "$RUNTIME_DIR/scripts/"
  cp -f "$REPO_ROOT/scripts/platform_matrix.env" "$RUNTIME_DIR/scripts/"
  for s in install_release.sh promote_release.sh write_worker_env.sh setup_gpu_node.sh \
           verify_e2e_node.sh verify_setup.sh verify_host_tools.sh verify_parabricks.sh verify_methyl_extractor.sh \
           install_host_tools_gpu_vm.sh \
           register_worker.sh build_release.sh package_methyl_extractor.sh \
           download_methyl_extractor_artifacts.sh assemble_release.sh bootstrap_goliath.sh \
           install_gateway_systemd.sh install_worker_systemd.sh \
           install_reclaim_leases_timer.sh provision_gateway_node.sh \
           provision_worker_node.sh preflight_worker_join.sh init_work_layout.sh; do
    [[ -f "$REPO_ROOT/scripts/$s" ]] && cp -f "$REPO_ROOT/scripts/$s" "$RUNTIME_DIR/scripts/"
  done
  chmod +x "$RUNTIME_DIR/scripts/"*.sh 2>/dev/null || true
}

write_manifest_stub() {
  local py_minor
  py_minor="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  cat >"$OUTPUT/manifest.json" <<EOF
{
  "version": "$VERSION",
  "python": "$py_minor",
  "parabricks_image": "${PARABRICKS_IMAGE:-}",
  "parabricks_image_digest": "",
  "docker_data_root": "/work/goliath/docker",
  "min_driver_version": "",
  "requirements_lock": "requirements-worker.lock",
  "runtime_bundle": "runtime-bundle",
  "artifacts": {
    "aarch64": {
      "methyl_extractor": "methyl-extractor-linux-aarch64.tar.gz",
      "sha256": ""
    },
    "amd64": {
      "methyl_extractor": "methyl-extractor-linux-amd64.tar.gz",
      "sha256": ""
    }
  }
}
EOF
  info "Wrote manifest stub: $OUTPUT/manifest.json"
  info "Add MethylExtractor tarballs and fill sha256 digests before promote."
}

if [[ "$SKIP_WHEELS" -eq 0 ]]; then
  build_wheels
  write_lockfile
fi
build_runtime_bundle
write_manifest_stub

info "Release build complete: $OUTPUT"
