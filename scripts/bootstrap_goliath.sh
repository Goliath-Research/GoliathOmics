#!/bin/bash
# Bootstrap MethylPipeline + MethylExtractor on shared storage (/work/goliath).
# Host-native worker provisioning: .venv, Python packages, MethylExtractor build, env files.
#
# Production path: use --release-dir to install from a versioned release (no git sources).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap_goliath.sh [options]

Options:
  --root PATH              Shared storage root (default: /work/goliath)
  --release-dir PATH       Production release directory (skips git clone/build; uses install_release.sh)
  --arch KEY               aarch64 or amd64 (default: detect); used with --release-dir
  --promote-release        Run promote_release.sh (extract, venv, flip current, worker.env)
  --pipeline-url URL       MethylPipeline git remote (dev/bootstrap path)
  --extractor-url URL      MethylExtractor git remote (dev/bootstrap path)
  --branch NAME            Git branch for both repos (default: current or main)
  --system-deps            Pass --system-deps to setup_host.sh
  --gpu                    Force GPU Python requirements
  --skip-docker            Skip Docker Engine / NVIDIA CTK install (verify-only)
  --skip-parabricks-pull   Do not docker pull Parabricks image (node join default)
  --skip-methyl-extractor  Skip MethylExtractor build
  --docker-data-root PATH  Shared Docker data-root (default: /work/goliath/docker with --release-dir)
  --dry-run                Print actions without executing
  --require-arc            Fail if Arc agent is not Connected (production workers)
  --skip-arc-check         Skip Arc prerequisite check even with --require-arc
  -h, --help               Show this help

Environment:
  GOLIATH_ROOT             Same as --root
  METHYL_PIPELINE_URL        Git URL for MethylPipeline
  METHYL_EXTRACTOR_URL       Git URL for MethylExtractor
  WORKER_API_BASE            Written to worker.env (default: http://localhost:8080/v1)
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
RELEASE_DIR=""
ARCH=""
PROMOTE_RELEASE=0
PIPELINE_URL="${METHYL_PIPELINE_URL:-}"
EXTRACTOR_URL="${METHYL_EXTRACTOR_URL:-}"
BRANCH=""
SYSTEM_DEPS=0
GPU_FLAG=0
SKIP_DOCKER=0
SKIP_PARABRICKS_PULL=0
SKIP_METHYL_EXTRACTOR=0
DOCKER_DATA_ROOT=""
DRY_RUN=0
REQUIRE_ARC=0
SKIP_ARC_CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --release-dir) RELEASE_DIR="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --promote-release) PROMOTE_RELEASE=1; shift ;;
    --pipeline-url) PIPELINE_URL="${2:-}"; shift 2 ;;
    --extractor-url) EXTRACTOR_URL="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --system-deps) SYSTEM_DEPS=1; shift ;;
    --gpu) GPU_FLAG=1; shift ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    --skip-parabricks-pull) SKIP_PARABRICKS_PULL=1; shift ;;
    --skip-methyl-extractor) SKIP_METHYL_EXTRACTOR=1; shift ;;
    --docker-data-root) DOCKER_DATA_ROOT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --require-arc) REQUIRE_ARC=1; shift ;;
    --skip-arc-check) SKIP_ARC_CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

UNAME_ARCH="$(detect_uname_arch)"
ARCH_KEY="${ARCH:-$(platform_arch_key "$UNAME_ARCH")}"
ME_SUBDIR="$(resolve_methyl_extractor_subdir "$UNAME_ARCH")"
PARABRICKS_IMAGE="${METHYL_PARABRICKS_IMAGE:-$(resolve_parabricks_image)}"

REPOS="$ROOT/repos"
PIPELINE_DIR="$REPOS/MethylPipeline"
EXTRACTOR_DIR="$REPOS/MethylExtractor"
VENV_DIR="$ROOT/venv-${ARCH_KEY}"
ENV_DIR="$ROOT/env"
DATA_DIR="$ROOT/data"
RUNS_DIR="$ROOT/runs"

info "Bootstrap goliath"
info "  root: $ROOT"
info "  arch: $UNAME_ARCH ($ARCH_KEY)"
info "  parabricks: ${PARABRICKS_IMAGE:-<unset>}"
if [[ -n "$RELEASE_DIR" ]]; then
  info "  mode: production release ($RELEASE_DIR)"
fi

WORK_ROOT="${METHYL_WORK_ROOT:-${WORK_ROOT:-/work}}"
if [[ -d "$WORK_ROOT" && -x "$SCRIPT_DIR/init_work_layout.sh" ]]; then
  INIT_ARGS=(--work "$WORK_ROOT")
  [[ "$DRY_RUN" -eq 1 ]] && INIT_ARGS+=(--dry-run)
  run bash "$SCRIPT_DIR/init_work_layout.sh" "${INIT_ARGS[@]}"
fi

run mkdir -p "$ENV_DIR" "$DATA_DIR" "$RUNS_DIR"

if [[ "$REQUIRE_ARC" -eq 1 && "$SKIP_ARC_CHECK" -eq 0 ]]; then
  run bash "$SCRIPT_DIR/verify_arc_prereqs.sh"
fi

if [[ -n "$RELEASE_DIR" ]]; then
  DOCKER_DATA_ROOT="${DOCKER_DATA_ROOT:-$ROOT/docker}"
  run mkdir -p "$DOCKER_DATA_ROOT"
  [[ -d "$RELEASE_DIR" ]] || die "Release directory not found: $RELEASE_DIR"
  SCRIPTS_DIR="$RELEASE_DIR/runtime-bundle/scripts"
  [[ -d "$SCRIPTS_DIR" ]] || SCRIPTS_DIR="$SCRIPT_DIR"

  if [[ "$SYSTEM_DEPS" -eq 1 ]]; then
    info "Running setup_host.sh --system-deps (no venv install) ..."
    SETUP_ARGS=(--system-deps --no-venv)
    [[ "$GPU_FLAG" -eq 1 ]] && SETUP_ARGS+=(--gpu)
    run bash "$SCRIPTS_DIR/setup_host.sh" "${SETUP_ARGS[@]}" 2>/dev/null || \
      run bash "$REPO_ROOT/scripts/setup_host.sh" "${SETUP_ARGS[@]}"
  fi

  if [[ "$PROMOTE_RELEASE" -eq 1 ]]; then
    PROMOTE_ARGS=(--root "$ROOT" --release "$RELEASE_DIR" --arch "$ARCH_KEY")
    [[ "$SKIP_PARABRICKS_PULL" -eq 0 ]] && PROMOTE_ARGS+=(--pull-parabricks) || PROMOTE_ARGS+=(--skip-docker-pull)
    run bash "$SCRIPTS_DIR/promote_release.sh" "${PROMOTE_ARGS[@]}"
  else
    run bash "$SCRIPTS_DIR/install_release.sh" --release-dir "$RELEASE_DIR" --venv "$VENV_DIR"
    ME_BIN="$ROOT/methyl-extractor-${ARCH_KEY}/bin/MethylExtractor"
    PLUGIN_DIR="$ROOT/methyl-extractor-${ARCH_KEY}/lib/hdf5_zstd_plugin"
    if [[ ! -x "$ME_BIN" ]]; then
      ME_BIN="${METHYL_EXTRACTOR_BIN:-MethylExtractor}"
      PLUGIN_DIR="${HDF5_PLUGIN_PATH:-/usr/local/hdf5/lib/plugin}"
      warn "MethylExtractor not at shared path; run promote_release.sh or extract tarball"
    fi
    if [[ -n "${WORKER_API_BASE:-}" ]]; then
      run bash "$SCRIPTS_DIR/write_worker_env.sh" --root "$ROOT" --manifest "$RELEASE_DIR/manifest.json" \
        --arch "$ARCH_KEY" --worker-api-base "$WORKER_API_BASE"
    else
      run bash "$SCRIPTS_DIR/write_worker_env.sh" --root "$ROOT" --manifest "$RELEASE_DIR/manifest.json" \
        --arch "$ARCH_KEY"
    fi
  fi

  # Node join installs Docker Engine + NVIDIA CTK locally; image layers stay under
  # shared DOCKER_DATA_ROOT (/work/goliath/docker). Parabricks pull is cluster-once
  # (promote host / --promote-release), not every Lambda join.
  if [[ "$SKIP_DOCKER" -eq 0 ]]; then
    GPU_ARGS=(--docker-data-root "$DOCKER_DATA_ROOT" --env-dir "$ENV_DIR")
    if [[ "$SKIP_PARABRICKS_PULL" -eq 0 && "$PROMOTE_RELEASE" -eq 0 ]]; then
      GPU_ARGS+=(--pull-parabricks)
    fi
    run bash "$SCRIPTS_DIR/setup_gpu_node.sh" "${GPU_ARGS[@]}" 2>/dev/null || \
      run bash "$REPO_ROOT/scripts/setup_gpu_node.sh" "${GPU_ARGS[@]}"
  elif [[ -n "$DOCKER_DATA_ROOT" ]]; then
    # Verify-only path when caller explicitly skipped Docker install
    GPU_ARGS=(--docker-data-root "$DOCKER_DATA_ROOT" --env-dir "$ENV_DIR" --skip-docker)
    run bash "$SCRIPTS_DIR/setup_gpu_node.sh" "${GPU_ARGS[@]}" 2>/dev/null || \
      run bash "$REPO_ROOT/scripts/setup_gpu_node.sh" "${GPU_ARGS[@]}" || true
  fi

  info "Bootstrap (release) complete."
  info "Next steps:"
  info "  1. source $ENV_DIR/worker.env"
  info "  2. Join enroll: $SCRIPTS_DIR/provision_worker_node.sh --finish-enroll (after Arc)"
  info "  3. Verify node: $SCRIPTS_DIR/verify_e2e_node.sh"
  exit 0
fi

clone_or_update() {
  local url="$1"
  local dest="$2"
  local name
  name="$(basename "$dest")"

  if [[ -z "$url" ]]; then
    if [[ -d "$dest/.git" ]]; then
      info "Using existing checkout: $dest"
      if [[ -n "$BRANCH" ]]; then
        run git -C "$dest" fetch origin
        run git -C "$dest" checkout "$BRANCH"
        run git -C "$dest" pull --ff-only origin "$BRANCH" || true
      fi
      return 0
    fi
    if [[ "$name" == "MethylPipeline" && -d "$REPO_ROOT/.git" ]]; then
      info "Seeding MethylPipeline from current checkout: $REPO_ROOT -> $dest"
      run mkdir -p "$(dirname "$dest")"
      if [[ ! -d "$dest" ]]; then
        run cp -a "$REPO_ROOT" "$dest"
      fi
      return 0
    fi
    die "No git URL for $name and $dest does not exist. Set METHYL_PIPELINE_URL / METHYL_EXTRACTOR_URL or clone manually."
  fi

  run mkdir -p "$(dirname "$dest")"
  if [[ -d "$dest/.git" ]]; then
    info "Updating $dest"
    run git -C "$dest" fetch origin
    if [[ -n "$BRANCH" ]]; then
      run git -C "$dest" checkout "$BRANCH"
      run git -C "$dest" pull --ff-only origin "$BRANCH" || true
    else
      run git -C "$dest" pull --ff-only || true
    fi
  else
    info "Cloning $url -> $dest"
    local clone_args=("$url" "$dest")
    if [[ -n "$BRANCH" ]]; then
      clone_args=(--branch "$BRANCH" "${clone_args[@]}")
    fi
    run git clone "${clone_args[@]}"
  fi
}

run mkdir -p "$REPOS"

clone_or_update "$PIPELINE_URL" "$PIPELINE_DIR"
clone_or_update "$EXTRACTOR_URL" "$EXTRACTOR_DIR"

VENV_DIR="$ROOT/venv"
SETUP_ARGS=(--venv "$VENV_DIR")
if [[ "$SYSTEM_DEPS" -eq 1 ]]; then
  SETUP_ARGS+=(--system-deps)
fi
if [[ "$GPU_FLAG" -eq 1 ]]; then
  SETUP_ARGS+=(--gpu)
fi

info "Running setup_host.sh ..."
run bash "$PIPELINE_DIR/scripts/setup_host.sh" "${SETUP_ARGS[@]}"

info "Running install_all.sh ..."
if [[ "$DRY_RUN" -eq 0 ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi
INSTALL_ARGS=(--pipeline-reqs)
if [[ "$GPU_FLAG" -eq 1 ]] || command -v nvidia-smi >/dev/null 2>&1; then
  INSTALL_ARGS+=(--gpu-reqs)
fi
run bash "$PIPELINE_DIR/scripts/install_all.sh" "${INSTALL_ARGS[@]}" --skip-marp

if [[ "$SKIP_METHYL_EXTRACTOR" -eq 0 && -d "$EXTRACTOR_DIR" ]]; then
  info "Building MethylExtractor ($ME_SUBDIR) ..."
  run bash -c "cd '$EXTRACTOR_DIR' && make"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    ME_BIN="$EXTRACTOR_DIR/build/dynamic/$ME_SUBDIR/MethylExtractor"
    if [[ ! -x "$ME_BIN" ]]; then
      die "MethylExtractor binary not found at $ME_BIN"
    fi
    PLUGIN_DIR="$EXTRACTOR_DIR/build/dynamic/$ME_SUBDIR/hdf5_zstd_plugin"
  else
    ME_BIN="$EXTRACTOR_DIR/build/dynamic/$ME_SUBDIR/MethylExtractor"
    PLUGIN_DIR="$EXTRACTOR_DIR/build/dynamic/$ME_SUBDIR/hdf5_zstd_plugin"
  fi
else
  ME_BIN="${METHYL_EXTRACTOR_BIN:-MethylExtractor}"
  PLUGIN_DIR="/usr/local/hdf5/lib/plugin"
fi

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  GPU_ARGS=(--env-dir "$ENV_DIR")
  if [[ -n "$DOCKER_DATA_ROOT" ]]; then
    GPU_ARGS+=(--docker-data-root "$DOCKER_DATA_ROOT")
  fi
  if [[ "$SKIP_PARABRICKS_PULL" -eq 0 ]]; then
    GPU_ARGS+=(--pull-parabricks)
  fi
  if [[ -x "$PIPELINE_DIR/scripts/setup_gpu_node.sh" ]]; then
    run bash "$PIPELINE_DIR/scripts/setup_gpu_node.sh" "${GPU_ARGS[@]}"
  fi
fi

WORKER_API_BASE="${WORKER_API_BASE:-}"
if [[ -z "$WORKER_API_BASE" && -f "$ENV_DIR/worker.env" ]]; then
  WORKER_API_BASE="$(grep -E '^[[:space:]]*WORKER_API_BASE=' "$ENV_DIR/worker.env" | tail -1 | cut -d= -f2- || true)"
  WORKER_API_BASE="${WORKER_API_BASE%$'\r'}"
  WORKER_API_BASE="${WORKER_API_BASE#\"}"
  WORKER_API_BASE="${WORKER_API_BASE%\"}"
fi
WORKER_PATH="$(expand_worker_path "$VENV_DIR")"

if [[ "$DRY_RUN" -eq 0 ]]; then
  cat >"$ENV_DIR/worker.env" <<EOF
# Generated by scripts/bootstrap_goliath.sh
# Note: EnvironmentFile= does not expand variables — PATH must be a literal value.
GOLIATH_ROOT=$ROOT
${WORKER_API_BASE:+WORKER_API_BASE=$WORKER_API_BASE}
# WORKER_ID=
# WORKER_TOKEN=
# WORKER_CAPABILITY=
METHYL_EXTRACTOR_BIN=$ME_BIN
HDF5_PLUGIN_PATH=$PLUGIN_DIR
PATH=$WORKER_PATH
EOF
  if [[ -n "$PARABRICKS_IMAGE" ]]; then
    echo "METHYL_PARABRICKS_IMAGE=$PARABRICKS_IMAGE" >>"$ENV_DIR/worker.env"
    echo 'METHYL_PARABRICKS_GPU_FLAGS="--gpus all"' >>"$ENV_DIR/worker.env"
  fi
  if [[ -f "$ENV_DIR/parabricks.env" ]]; then
    echo "# Also see parabricks.env" >>"$ENV_DIR/worker.env"
  fi
  info "Wrote $ENV_DIR/worker.env"
else
  info "[DRY-RUN] Would write $ENV_DIR/worker.env"
fi

info "Bootstrap complete."
info "Next steps:"
info "  1. source $ENV_DIR/worker.env"
info "  2. Enroll worker: methyl-worker enroll --api-base \"\$WORKER_API_BASE\" --cluster goliath --key \"\$(hostname -s)\""
info "  3. Verify node: $PIPELINE_DIR/scripts/verify_e2e_node.sh"
