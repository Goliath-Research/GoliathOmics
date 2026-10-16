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
  --wheel-arches LIST  Linux arches to bundle third-party wheels for
                       (default: "x86_64 aarch64"; env GOLIATH_WHEEL_ARCHES)
  --skip-third-party   Do not download locked third-party wheels (dev only;
                       the release then needs PyPI at install time)
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
WHEEL_ARCHES="${GOLIATH_WHEEL_ARCHES:-x86_64 aarch64}"
WORKER_PY_VERSION="${GOLIATH_WORKER_PYTHON_VERSION:-3.12}"
SKIP_THIRD_PARTY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --python) PYTHON_BIN="${2:-}"; shift 2 ;;
    --skip-wheels) SKIP_WHEELS=1; shift ;;
    --with-gpu-reqs) WITH_GPU=1; shift ;;
    --wheel-arches) WHEEL_ARCHES="${2:-}"; shift 2 ;;
    --skip-third-party) SKIP_THIRD_PARTY=1; shift ;;
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

_resolve_pkg_path() {
  local name="$1"
  # Do not fall through to workers/. A missing pyproject used to resolve
  # workflow_engine as the worker tree and the release then omitted the
  # methyl-gateway wheel.
  local candidates=(
    "$REPO_ROOT/packages/$name"
    "$REPO_ROOT/$name"
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
    if [[ "$line" == "workflow_engine" ]]; then
      info "  wheel: workflow_engine (methyl-gateway console scripts)"
      bash "$SCRIPT_DIR/build_gateway_wheel.sh" \
        --version "$VERSION" \
        --wheels "$WHEELS_DIR" \
        --sha-file "$OUTPUT/.gateway-source-sha"
      continue
    fi
    pkg_path="$(_resolve_pkg_path "$line")" || die "Package not found for packages.list entry: $line"
    info "  wheel: $line ($pkg_path)"
    "$PYTHON_BIN" -m pip wheel "$pkg_path" -w "$WHEELS_DIR" --no-deps
  done < "$REPO_ROOT/scripts/packages.list"
  bash "$SCRIPT_DIR/check_gateway_wheel.sh" "$WHEELS_DIR"
}

write_lockfile() {
  local req_combined="$OUTPUT/requirements-worker.in"
  cat "$REPO_ROOT/requirements-pipeline.txt" >"$req_combined"
  if [[ "$WITH_GPU" -eq 1 && -f "$REPO_ROOT/requirements-gpu-cuda12.txt" ]]; then
    echo "" >>"$req_combined"
    cat "$REPO_ROOT/requirements-gpu-cuda12.txt" >>"$req_combined"
  fi
  info "Compiling requirements-worker.lock"
  # --find-links is needed to resolve the first-party wheels, but must not be
  # written into the lock: pip-compile would emit the absolute build path
  # (e.g. /home/runner/work/_temp/release/wheels) and every pod would then
  # search that non-existent runner directory. Installers pass --find-links
  # for their own local wheels dir.
  if "$PYTHON_BIN" -m pip install -q pip-tools 2>/dev/null; then
    "$PYTHON_BIN" -m piptools compile "$req_combined" \
      -o "$OUTPUT/requirements-worker.lock" \
      --find-links "$WHEELS_DIR" \
      --no-emit-find-links \
      --no-emit-index-url \
      --allow-unsafe \
      --strip-extras \
      --generate-hashes 2>/dev/null || \
    "$PYTHON_BIN" -m piptools compile "$req_combined" \
      -o "$OUTPUT/requirements-worker.lock" \
      --find-links "$WHEELS_DIR" \
      --no-emit-find-links \
      --no-emit-index-url
  else
    warn_fallback_lock "$req_combined"
  fi
  strip_lock_build_paths "$OUTPUT/requirements-worker.lock"
}

# Drop any pip option lines (-f/--find-links/--index-url/...) from the lock.
# Where to find packages is the installer's business, never the build host's.
strip_lock_build_paths() {
  local lock="$1"
  if grep -Eq '^[[:space:]]*(-f|--find-links|-i|--index-url|--extra-index-url|--trusted-host)([[:space:]=]|$)' "$lock"; then
    info "Removing build-host find-links/index lines from $(basename "$lock")"
    sed -E -i '/^[[:space:]]*(-f|--find-links|-i|--index-url|--extra-index-url|--trusted-host)([[:space:]=]|$)/d' "$lock"
  fi
  if grep -q "$WHEELS_DIR\|file://" "$lock"; then
    die "requirements-worker.lock still references a build-host path ($WHEELS_DIR or file://)"
  fi
}

warn_fallback_lock() {
  local req_in="$1"
  info "pip-tools not available; writing requirements-worker.lock from wheels + base reqs"
  {
    cat "$req_in"
    echo ""
    echo "# Local goliath wheels (installers pass --find-links <release>/wheels)"
    for whl in "$WHEELS_DIR"/*.whl; do
      [[ -f "$whl" ]] || continue
      # name-version-... ; pin by version, never by build-host file:// path.
      basename "$whl" | awk -F- '{print $1 "==" $2}'
    done
  } >"$OUTPUT/requirements-worker.lock"
}

# Download every locked third-party wheel for each worker arch (manylinux,
# CPython $WORKER_PY_VERSION) so pods can install offline. Packages that only
# publish an sdist are built here; that yields a usable wheel only when the
# package is pure Python (py3-none-any). Anything still missing fails the build.
download_locked_wheels() {
  local lock="$OUTPUT/requirements-worker.lock" arch plat pyabi missing pin hash_args=()
  [[ -f "$lock" ]] || die "missing $lock"
  pyabi="cp${WORKER_PY_VERSION/./}"
  grep -q -- '--hash=' "$lock" && hash_args=(--require-hashes)
  for arch in $WHEEL_ARCHES; do
    info "Downloading locked wheels for linux ${arch} ${pyabi}"
    local plats=()
    for plat in manylinux_2_17 manylinux2014 manylinux_2_28 manylinux_2_31 manylinux_2_34 manylinux_2_35; do
      plats+=(--platform "${plat}_${arch}")
    done
    # One pip call per arch; --no-deps because the lock is already complete.
    # A failure here usually means an sdist-only package; handled below.
    "$PYTHON_BIN" -m pip download \
      --dest "$WHEELS_DIR" \
      --find-links "$WHEELS_DIR" \
      --only-binary=:all: \
      "${plats[@]}" \
      --platform "linux_${arch}" \
      --implementation cp \
      --python-version "$WORKER_PY_VERSION" \
      --abi "$pyabi" --abi abi3 --abi none \
      --no-deps "${hash_args[@]}" \
      -r "$lock" || info "  pip download for ${arch} was incomplete; checking which pins are missing"
    missing="$("$PYTHON_BIN" "$SCRIPT_DIR/verify_locked_wheels.py" "$lock" "$WHEELS_DIR" \
      --arch "$arch" --python-version "$WORKER_PY_VERSION" --list-missing || true)"
    for pin in $missing; do
      info "  no ${arch} wheel on the index for ${pin}; building from sdist"
      "$PYTHON_BIN" -m pip wheel --no-deps --no-binary=:all: -w "$WHEELS_DIR" "$pin" \
        || info "  could not build ${pin} from sdist"
    done
  done
}

verify_locked_wheels() {
  local args=() arch
  for arch in $WHEEL_ARCHES; do args+=(--arch "$arch"); done
  info "Verifying every pin in requirements-worker.lock has a wheel for: $WHEEL_ARCHES"
  "$PYTHON_BIN" "$SCRIPT_DIR/verify_locked_wheels.py" "$OUTPUT/requirements-worker.lock" "$WHEELS_DIR" \
    "${args[@]}" --python-version "$WORKER_PY_VERSION" \
    || die "release wheels/ is incomplete (see MISSING lines). A native package with no wheel for an arch must be built on that arch or dropped from the lock."
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
           register_worker.sh build_release.sh package_methyl_extractor.sh bundle_methyl_extractor_libs.sh \
           download_methyl_extractor_artifacts.sh assemble_release.sh bootstrap_goliath.sh \
           install_gateway_systemd.sh install_gateway_venv.sh install_worker_systemd.sh \
           install_reclaim_leases_timer.sh provision_gateway_node.sh \
           setup_gateway_nginx.sh \
           provision_worker_node.sh preflight_worker_join.sh init_work_layout.sh \
           start_shared_worker.sh \
           install_methyl_extractor_tarball.sh methyl_extractor_local.sh worker_venv_local.sh \
           verify_locked_wheels.py \
           deploy_workflow_definitions.sh deploy_mc_workflow_definitions.sh \
           deploy_process_pack_catalog.sh deploy_target.sh \
           report_deploy.sh rollouts_hook.sh default_methyl_site.json; do
    if [[ -f "$REPO_ROOT/scripts/$s" ]]; then
      cp -f "$REPO_ROOT/scripts/$s" "$RUNTIME_DIR/scripts/"
    fi
  done
  chmod +x "$RUNTIME_DIR/scripts/"*.sh "$RUNTIME_DIR/scripts/"*.py 2>/dev/null || true
}

write_manifest_stub() {
  local py_minor gateway_sha=""
  py_minor="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ -f "$OUTPUT/.gateway-source-sha" ]]; then
    gateway_sha="$(tr -d '[:space:]' <"$OUTPUT/.gateway-source-sha")"
    rm -f "$OUTPUT/.gateway-source-sha"
  fi
  if [[ -n "$gateway_sha" && ! "$gateway_sha" =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "gateway source revision is not safe to record: $gateway_sha"
  fi
  cat >"$OUTPUT/manifest.json" <<EOF
{
  "version": "$VERSION",
  "python": "$py_minor",
  "gateway_source": "$gateway_sha",
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
  if [[ "$SKIP_THIRD_PARTY" -eq 0 ]]; then
    download_locked_wheels
    verify_locked_wheels
  fi
fi
build_runtime_bundle
write_manifest_stub

info "Release build complete: $OUTPUT"
