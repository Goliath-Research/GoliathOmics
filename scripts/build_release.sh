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
                       (default: "x86_64"; add aarch64 to opt in; env GOLIATH_WHEEL_ARCHES)
  --strict-wheels      (default, kept for compatibility) fail the release when a
                       locked pin has no bundled wheel
  --allow-missing-wheels  Dev only: warn instead of failing on missing wheels
  --system-deps-targets LIST  OS targets to bundle system tools for as offline
                       .deb sets (default: derived from deploy/targets/*.json
                       worker images via derive_system_deps_targets.py; env
                       GOLIATH_SYSTEM_DEPS_TARGETS overrides); needs docker/podman
  --skip-system-deps   Dev only: do not bundle system tools
  --system-deps-check-images LIST  Worker images the bundle must install in
                       offline (default: the worker images of
                       deploy/targets/*.json; env GOLIATH_SYSDEPS_CHECK_IMAGES,
                       "" for none). A large image is skipped with a warning
                       when the container storage lacks room
  --require-worker-image-check  Fail instead of skipping a worker image that
                       does not fit (env GOLIATH_SYSDEPS_REQUIRE_WORKER_IMAGES=1)
  --skip-system-deps-check  Dev only: do not prove the bundle installs offline
  --extra-index-url U  Extra index for lock compile and wheel download; repeatable
                       (default: https://pypi.nvidia.com; env
                       GOLIATH_PIP_EXTRA_INDEX_URLS, space-separated, "" for none)
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
WHEEL_ARCHES="${GOLIATH_WHEEL_ARCHES:-x86_64}"
STRICT_WHEELS="${GOLIATH_STRICT_WHEELS:-1}"
# Default: every worker OS/arch in deploy/targets (today: ubuntu-22.04-amd64
# for ghcr.io/.../goliath-worker). Explicit env/flag wins.
if [[ -n "${GOLIATH_SYSTEM_DEPS_TARGETS:-}" ]]; then
  SYSTEM_DEPS_TARGETS="$GOLIATH_SYSTEM_DEPS_TARGETS"
else
  SYSTEM_DEPS_TARGETS="$("$SCRIPT_DIR/derive_system_deps_targets.py" --targets-dir "$REPO_ROOT/deploy/targets")"     || die "could not derive system-deps targets from deploy/targets"
fi
SKIP_SYSTEM_DEPS=0
SKIP_SYSTEM_DEPS_CHECK=0
EXTRA_INDEX_URLS="${GOLIATH_PIP_EXTRA_INDEX_URLS-https://pypi.nvidia.com}"
_cli_extra_index=""
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
    --strict-wheels) STRICT_WHEELS=1; shift ;;
    --allow-missing-wheels) STRICT_WHEELS=0; shift ;;
    --system-deps-targets) SYSTEM_DEPS_TARGETS="${2:-}"; shift 2 ;;
    --skip-system-deps) SKIP_SYSTEM_DEPS=1; shift ;;
    --system-deps-check-images) export GOLIATH_SYSDEPS_CHECK_IMAGES="${2:-}"; shift 2 ;;
    --require-worker-image-check) export GOLIATH_SYSDEPS_REQUIRE_WORKER_IMAGES=1; shift ;;
    --skip-system-deps-check) SKIP_SYSTEM_DEPS_CHECK=1; shift ;;
    --extra-index-url) _cli_extra_index="${_cli_extra_index} ${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$VERSION" ]] || die "--version is required"
[[ -n "$_cli_extra_index" ]] && EXTRA_INDEX_URLS="$_cli_extra_index"
# Same index config for lock compile and wheel download (NVIDIA/RAPIDS pins).
EXTRA_INDEX_ARGS=()
for _u in $EXTRA_INDEX_URLS; do EXTRA_INDEX_ARGS+=(--extra-index-url "$_u"); done
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
        --sha-file "$OUTPUT/.gateway-source-sha" \
        --sql-out "$OUTPUT/goliathapp-sql"
      continue
    fi
    pkg_path="$(_resolve_pkg_path "$line")" || die "Package not found for packages.list entry: $line"
    info "  wheel: $line ($pkg_path)"
    "$PYTHON_BIN" -m pip wheel "$pkg_path" -w "$WHEELS_DIR" --no-deps
  done < "$REPO_ROOT/scripts/packages.list"
  # Poetry path dependencies end up as "name @ file:///<runner>/packages/x"
  # in wheel metadata; pin them to the sibling wheel's version instead.
  "$PYTHON_BIN" "$SCRIPT_DIR/fix_wheel_path_deps.py" "$WHEELS_DIR"
  bash "$SCRIPT_DIR/check_gateway_wheel.sh" "$WHEELS_DIR"
  local omics_sha="local-source"
  if git -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1; then
    omics_sha="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  fi
  info "Bundling GoliathOmics domain sql_pg ($omics_sha)"
  "$PYTHON_BIN" "$SCRIPT_DIR/bundle_omics_sql.py" \
    --sql-dir "$REPO_ROOT/workflow_engine/sql_pg" \
    --out "$OUTPUT/omics-sql" \
    --commit "$omics_sha"
}

# name==version for every wheel built from this repo (wheels/ holds only
# first-party wheels at this point; third-party ones are downloaded later).
write_first_party_list() {
  local whl
  : >"$OUTPUT/first-party-wheels.txt"
  for whl in "$WHEELS_DIR"/*.whl; do
    [[ -f "$whl" ]] || continue
    basename "$whl" | awk -F- '{print $1 "==" $2}' >>"$OUTPUT/first-party-wheels.txt"
  done
  sort -u -o "$OUTPUT/first-party-wheels.txt" "$OUTPUT/first-party-wheels.txt"
  info "first-party wheels: $(wc -l <"$OUTPUT/first-party-wheels.txt")"
}

write_lockfile() {
  local req_combined="$OUTPUT/requirements-worker.in"
  cat "$REPO_ROOT/requirements-pipeline.txt" >"$req_combined"
  if [[ "$WITH_GPU" -eq 1 && -f "$REPO_ROOT/requirements-gpu-cuda12.txt" ]]; then
    echo "" >>"$req_combined"
    cat "$REPO_ROOT/requirements-gpu-cuda12.txt" >>"$req_combined"
  fi
  # First-party wheels (built above into wheels/) are not in
  # requirements-pipeline.txt. Pin them by version so the lock carries them
  # and their dependencies; without this the worker venv had no methyl_worker.
  write_first_party_list
  if [[ -s "$OUTPUT/first-party-wheels.txt" ]]; then
    { echo ""; echo "# first-party wheels from wheels/"; cat "$OUTPUT/first-party-wheels.txt"; } >>"$req_combined"
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
      "${EXTRA_INDEX_ARGS[@]}" \
      --no-emit-find-links \
      --no-emit-index-url \
      --allow-unsafe \
      --strip-extras \
      --generate-hashes 2>/dev/null || \
    "$PYTHON_BIN" -m piptools compile "$req_combined" \
      -o "$OUTPUT/requirements-worker.lock" \
      --find-links "$WHEELS_DIR" \
      "${EXTRA_INDEX_ARGS[@]}" \
      --no-emit-find-links \
      --no-emit-index-url
  else
    warn_fallback_lock "$req_combined"
  fi
  strip_lock_build_paths "$OUTPUT/requirements-worker.lock"
}

# Make the lock build-host independent: drop find-links/index option lines,
# rewrite first-party "name @ file:///<build>/wheels/x.whl" pins to
# name==version (the wheels ship in wheels/), and scrub paths from comments.
# Where to find packages is the installer's business, never the build host's.
strip_lock_build_paths() {
  local lock="$1"
  "$PYTHON_BIN" "$SCRIPT_DIR/sanitize_lock.py" "$lock" --wheels-dir "$WHEELS_DIR" \
    || die "requirements-worker.lock still references a build-host path ($WHEELS_DIR or file://); see BUILD-HOST PATH lines"
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

# Best-effort: download every locked third-party wheel for each arch in
# WHEEL_ARCHES (manylinux, CPython $WORKER_PY_VERSION) so pods can install
# offline. Prebuilt binary wheels only (--only-binary=:all:): nothing is ever
# compiled on the runner. Building sdists here (the old fallback) compiled
# even pure-Python pins, failed native ones on missing system libs, and put
# locally built wheels into wheels/ whose sha256 no longer matched the lock
# ("has bad hash"). pip download resolves a whole requirements file
# atomically, so one unavailable pin used to leave the batch empty; after the
# batch, each pin still missing is retried on its own, with its lock hashes.
# Nothing here aborts the build; verify_locked_wheels reports what is missing.
download_locked_wheels() {
  local lock="$OUTPUT/requirements-worker.lock" arch plat pyabi missing pin pin_req
  local hash_args=() dl_args=()
  [[ -f "$lock" ]] || die "missing $lock"
  pyabi="cp${WORKER_PY_VERSION/./}"
  grep -q -- '--hash=' "$lock" && hash_args=(--require-hashes)
  pin_req="$(mktemp)"
  for arch in $WHEEL_ARCHES; do
    info "Downloading locked wheels for linux ${arch} ${pyabi} (binary wheels only)"
    dl_args=(--dest "$WHEELS_DIR" --find-links "$WHEELS_DIR" "${EXTRA_INDEX_ARGS[@]}"
      --only-binary=:all: --implementation cp --python-version "$WORKER_PY_VERSION"
      --abi "$pyabi" --abi abi3 --abi none --platform any --platform "linux_${arch}" --no-deps)
    # pip matches --platform tags exactly; list every manylinux level a
    # glibc 2.17+ worker accepts (e.g. nvidia-nccl-cu13 ships only
    # manylinux_2_27, scipy manylinux_2_27.manylinux_2_28).
    for plat in manylinux1 manylinux2010 manylinux2014 manylinux_2_17 manylinux_2_24 manylinux_2_27 \
                manylinux_2_28 manylinux_2_31 manylinux_2_34 manylinux_2_35; do
      [[ "$arch" != x86_64 && "$plat" =~ ^manylinux(1|2010)$ ]] && continue
      dl_args+=(--platform "${plat}_${arch}")
    done
    "$PYTHON_BIN" -m pip download "${dl_args[@]}" "${hash_args[@]}" -r "$lock" \
      || info "  batch download for ${arch} incomplete; retrying missing pins one by one"
    missing="$("$PYTHON_BIN" "$SCRIPT_DIR/verify_locked_wheels.py" "$lock" "$WHEELS_DIR" \
      --arch "$arch" --python-version "$WORKER_PY_VERSION" --list-missing 2>/dev/null || true)"
    for pin in $missing; do
      "$PYTHON_BIN" "$SCRIPT_DIR/verify_locked_wheels.py" "$lock" "$WHEELS_DIR" --pin-block "$pin" >"$pin_req"
      "$PYTHON_BIN" -m pip download -q "${dl_args[@]}" "${hash_args[@]}" -r "$pin_req" >/dev/null 2>&1 \
        || info "  no ${arch} binary wheel available for ${pin}; pods will fetch it from the index"
    done
  done
  rm -f "$pin_req"
}

# pip/setuptools/wheel/packaging for the worker venv itself
# (requirements-venv-bootstrap.txt): pure-Python wheels into wheels/, plus
# venv-bootstrap.lock pinning each to the sha256 of the bundled file, so
# install_release.sh upgrades a fresh venv offline instead of from PyPI.
bundle_venv_bootstrap_wheels() {
  local req="$REPO_ROOT/requirements-venv-bootstrap.txt" lock="$OUTPUT/venv-bootstrap.lock"
  [[ -f "$req" ]] || die "missing $req"
  info "Downloading venv bootstrap wheels ($(grep -cE '^[A-Za-z0-9]' "$req") pins)"
  "$PYTHON_BIN" -m pip download -q --dest "$WHEELS_DIR" --only-binary=:all: --no-deps \
    --implementation py --python-version "$WORKER_PY_VERSION" --platform any --abi none \
    -r "$req" || die "could not download the venv bootstrap wheels in $req"
  "$PYTHON_BIN" - "$req" "$WHEELS_DIR" "$lock" <<'PY' || die "could not write venv-bootstrap.lock"
import hashlib, re, sys
from pathlib import Path
req, wheels, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
norm = lambda n: re.sub(r"[-_.]+", "_", n).lower()
lines = ["# Generated by build_release.sh from requirements-venv-bootstrap.txt.",
         "# install_release.sh: pip install --no-index --find-links wheels/ --require-hashes -r venv-bootstrap.lock"]
for raw in req.read_text().splitlines():
    pin = raw.split("#", 1)[0].strip()
    if not pin:
        continue
    name, _, ver = pin.partition("==")
    if not ver:
        sys.exit(f"{req}: '{pin}' must be pinned with ==")
    found = [w for w in wheels.glob("*.whl")
             if norm(w.name.split("-")[0]) == norm(name) and w.name.split("-")[1] == ver]
    if not found:
        sys.exit(f"no bundled wheel for {pin} in {wheels}")
    hashes = sorted({hashlib.sha256(w.read_bytes()).hexdigest() for w in found})
    lines.append(f"{name}=={ver} " + " ".join(f"--hash=sha256:{h}" for h in hashes))
out.write_text("\n".join(lines) + "\n")
PY
  info "  wrote $lock"
}

# Pins with no binary wheel on any index (python-louvain==0.16 ships only an
# sdist) get a wheel built here from the hash-checked sdist; the built wheel's
# sha256 is added to the lock so pods can still --require-hashes.
build_sdist_wheels() {
  local args=() arch
  for arch in $WHEEL_ARCHES; do args+=(--arch "$arch"); done
  info "Building wheels for sdist-only pins"
  "$PYTHON_BIN" "$SCRIPT_DIR/bundle_sdist_wheels.py" "$OUTPUT/requirements-worker.lock" "$WHEELS_DIR" \
    "${args[@]}" --python-version "$WORKER_PY_VERSION" --python "$PYTHON_BIN" -- "${EXTRA_INDEX_ARGS[@]}" \
    || info "  some sdist-only pins could not be built (verify below decides)"
}

# Prove the worker venv installs offline (--no-index --find-links wheels/)
# with the full lock, for every bundled arch.
check_offline_wheels() {
  local args=() arch
  for arch in $WHEEL_ARCHES; do args+=(--arch "$arch"); done
  bash "$SCRIPT_DIR/check_offline_wheels.sh" "$OUTPUT/requirements-worker.lock" "$WHEELS_DIR" \
    "${args[@]}" --python-version "$WORKER_PY_VERSION" --python "$PYTHON_BIN" \
    --bootstrap-lock "$OUTPUT/venv-bootstrap.lock" \
    || die "release wheels/ cannot install the worker venv offline (see above)"
}

# System tools (scripts/system_tools.list) as offline .deb bundles under
# system-deps/<target>/; pods install them with install_system_deps.sh --bundle.
# Each bundle must then install with --network none in a stock image, the
# oldest package set of its OS release, and the worker images
# (check_system_deps_bundle.sh); a bundle that does not fails the release.
bundle_system_deps() {
  local t
  for t in $SYSTEM_DEPS_TARGETS; do
    bash "$SCRIPT_DIR/bundle_system_deps.sh" --target "$t" --output "$OUTPUT/system-deps" \
      || die "could not bundle system tools for $t"
    if [[ "$SKIP_SYSTEM_DEPS_CHECK" -eq 1 ]]; then
      info "  skipping the offline install check of the $t bundle (--skip-system-deps-check)"
      continue
    fi
    bash "$SCRIPT_DIR/check_system_deps_bundle.sh" --bundle "$OUTPUT/system-deps" --target "$t" \
      || die "the $t system-deps bundle does not install offline (see check-system-deps above)"
  done
}

verify_locked_wheels() {
  local args=() arch
  for arch in $WHEEL_ARCHES; do args+=(--arch "$arch"); done
  [[ "$STRICT_WHEELS" == "1" ]] && args+=(--strict)
  info "Verifying bundled wheels for: $WHEEL_ARCHES (strict=${STRICT_WHEELS})"
  "$PYTHON_BIN" "$SCRIPT_DIR/verify_locked_wheels.py" "$OUTPUT/requirements-worker.lock" "$WHEELS_DIR" \
    "${args[@]}" --python-version "$WORKER_PY_VERSION" \
    || die "release wheels/ is incomplete (see MISSING lines) and --strict-wheels is set"
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
    # Product fixtures, profiles, analytes, and checks only. The platform
    # compiler and DomainProgram IR ship in the methyl-gateway wheel
    # (domain.compiler / domain.program). Copying them here shadowed that
    # package on workers that put runtime-bundle/domain on PYTHONPATH.
    "${rsync_safe[@]}" \
      --exclude '/compiler.py' \
      --exclude '/workflow_context.py' \
      --exclude '/verify_workflow.py' \
      --exclude '/program.py' \
      --exclude '/__init__.py' \
      --exclude '__pycache__/' \
      "$REPO_ROOT/workflow_engine/domain/" "$RUNTIME_DIR/domain/"
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
           start_shared_worker.sh check_local_scratch.sh install_system_deps.sh system_tools.list goliath_align_local.sh check_goliath_align_host.sh download_goliath_align_artifacts.sh bundle_system_deps.sh \
           check_system_deps_bundle.sh \
           bundle_sdist_wheels.py check_offline_wheels.sh \
           install_methyl_extractor_tarball.sh methyl_extractor_local.sh worker_venv_local.sh \
           verify_locked_wheels.py sanitize_lock.py fix_wheel_path_deps.py \
           deploy_workflow_definitions.sh deploy_mc_workflow_definitions.sh \
           deploy_process_pack_catalog.sh deploy_target.sh \
           apply_goliathapp_schema.sh apply_omics_schema.sh \
           bundle_goliathapp_sql.py bundle_omics_sql.py \
           report_deploy.sh rollouts_hook.sh default_methyl_site.json; do
    if [[ -f "$REPO_ROOT/scripts/$s" ]]; then
      cp -f "$REPO_ROOT/scripts/$s" "$RUNTIME_DIR/scripts/"
    fi
  done
  chmod +x "$RUNTIME_DIR/scripts/"*.sh "$RUNTIME_DIR/scripts/"*.py 2>/dev/null || true
}

write_manifest_stub() {
  local py_minor gateway_sha="" sql_json="null"
  py_minor="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  if [[ -f "$OUTPUT/.gateway-source-sha" ]]; then
    gateway_sha="$(tr -d '[:space:]' <"$OUTPUT/.gateway-source-sha")"
    rm -f "$OUTPUT/.gateway-source-sha"
  fi
  if [[ -n "$gateway_sha" && ! "$gateway_sha" =~ ^[A-Za-z0-9._-]+$ ]]; then
    die "gateway source revision is not safe to record: $gateway_sha"
  fi
  # GoliathApp sql_pg bundle (build_gateway_wheel.sh --sql-out). Deploy
  # applies it once per release (scripts/apply_goliathapp_schema.sh).
  local omics_sql_json="null"
  if [[ -f "$OUTPUT/goliathapp-sql/source.json" ]]; then
    sql_json="$("$PYTHON_BIN" -c '
import json, sys
src = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"path": "goliathapp-sql", "manifest": "goliathapp-sql/manifest.sha256",
                  "commit": src.get("commit", ""), "scripts": src.get("scripts", 0)}))
' "$OUTPUT/goliathapp-sql/source.json")"
  fi
  if [[ -f "$OUTPUT/omics-sql/source.json" ]]; then
    omics_sql_json="$("$PYTHON_BIN" -c '
import json, sys
src = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"path": "omics-sql", "manifest": "omics-sql/manifest.sha256",
                  "commit": src.get("commit", ""), "scripts": src.get("scripts", 0)}))
' "$OUTPUT/omics-sql/source.json")"
  fi
  cat >"$OUTPUT/manifest.json" <<EOF
{
  "version": "$VERSION",
  "python": "$py_minor",
  "gateway_source": "$gateway_sha",
  "goliathapp_sql": $sql_json,
  "omics_sql": $omics_sql_json,
  "parabricks_image": "${PARABRICKS_IMAGE:-}",
  "parabricks_image_digest": "",
  "docker_data_root": "/work/goliath/docker",
  "min_driver_version": "",
  "requirements_lock": "requirements-worker.lock",
  "runtime_bundle": "runtime-bundle",
  "system_deps": "system-deps",
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
    bundle_venv_bootstrap_wheels
    build_sdist_wheels
    verify_locked_wheels
    if [[ "$STRICT_WHEELS" == "1" ]]; then
      check_offline_wheels
    fi
  fi
fi
if [[ "$SKIP_SYSTEM_DEPS" -eq 0 && -n "$SYSTEM_DEPS_TARGETS" ]]; then
  bundle_system_deps
fi
build_runtime_bundle
write_manifest_stub

info "Release build complete: $OUTPUT"
