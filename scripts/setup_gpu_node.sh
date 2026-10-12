#!/bin/bash
# Install Docker Engine and NVIDIA Container Toolkit on Ubuntu GPU worker nodes.
# Optionally configure shared data-root and pull Clara Parabricks.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup_gpu_node.sh [options]

Options:
  --pull-parabricks       Pull METHYL_PARABRICKS_IMAGE after setup (release promote, not node join)
  --docker-data-root PATH Set Docker data-root to shared storage (e.g. /work/goliath/docker)
  --env-dir PATH          Write parabricks.env (default: /work/goliath/env)
  --skip-docker           Skip Docker / NVIDIA toolkit install (verify only)
  -h, --help              Show this help

Prerequisites:
  - Ubuntu/Debian with apt-get
  - NVIDIA driver (nvidia-smi must work)
  - sudo for package installation

Environment:
  METHYL_PARABRICKS_IMAGE   Override Parabricks image (else platform_matrix.env)
  PLATFORM_MATRIX_FILE      Path to scripts/platform_matrix.env

Notes:
  - Use --docker-data-root on every GPU VM so all nodes share image layers on fast storage.
  - Run --pull-parabricks only during release promote (scripts/promote_release.sh), not on each node join.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

PULL_PARABRICKS=0
SKIP_DOCKER=0
DOCKER_DATA_ROOT=""
ENV_DIR="${GOLIATH_ENV_DIR:-/work/goliath/env}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull-parabricks) PULL_PARABRICKS=1; shift ;;
    --docker-data-root) DOCKER_DATA_ROOT="${2:-}"; shift 2 ;;
    --env-dir) ENV_DIR="${2:-}"; shift 2 ;;
    --skip-docker) SKIP_DOCKER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

if ! command -v nvidia-smi >/dev/null 2>&1; then
  die "nvidia-smi not found. Install NVIDIA drivers before GPU node setup."
fi
info "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo unknown)"

sudo_cmd=""
if [[ "$(id -u)" -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || die "sudo required for package installation"
  sudo_cmd="sudo"
fi

configure_docker_data_root() {
  local root="${1:?data-root path required}"
  root="$(readlink -f "$root" 2>/dev/null || echo "$root")"
  info "Configuring Docker data-root: $root"
  mkdir -p "$root"
  local daemon_json="/etc/docker/daemon.json"
  if [[ -f "$daemon_json" ]]; then
    if command -v python3 >/dev/null 2>&1; then
      $sudo_cmd python3 - <<PY "$root" "$daemon_json"
import json, sys
root, path = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except (json.JSONDecodeError, OSError):
    data = {}
if data.get("data-root") == root:
    sys.exit(0)
data["data-root"] = root
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
    else
      warn "python3 not found; writing minimal daemon.json"
      echo "{\"data-root\": \"$root\"}" | $sudo_cmd tee "$daemon_json" >/dev/null
    fi
  else
    $sudo_cmd mkdir -p /etc/docker
    echo "{\"data-root\": \"$root\"}" | $sudo_cmd tee "$daemon_json" >/dev/null
  fi
  if systemctl is-active docker >/dev/null 2>&1; then
    $sudo_cmd systemctl restart docker || $sudo_cmd service docker restart || true
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    info "docker already installed: $(docker --version)"
    return 0
  fi
  info "Installing Docker Engine..."
  $sudo_cmd apt-get update
  $sudo_cmd apt-get install -y ca-certificates curl gnupg
  $sudo_cmd install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $sudo_cmd gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    $sudo_cmd chmod a+r /etc/apt/keyrings/docker.gpg
  fi
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "${VERSION_CODENAME:-jammy}") stable" | \
    $sudo_cmd tee /etc/apt/sources.list.d/docker.list >/dev/null
  $sudo_cmd apt-get update
  $sudo_cmd apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  $sudo_cmd systemctl enable --now docker || true
  if [[ "$(id -u)" -ne 0 ]] && ! groups | grep -q docker; then
    warn "Add user to docker group: sudo usermod -aG docker $USER && newgrp docker"
  fi
}

install_nvidia_container_toolkit() {
  if command -v nvidia-ctk >/dev/null 2>&1; then
    info "nvidia-ctk already installed"
  else
    info "Installing NVIDIA Container Toolkit..."
    $sudo_cmd apt-get update
    $sudo_cmd apt-get install -y curl gnupg
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
      $sudo_cmd gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      $sudo_cmd tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    $sudo_cmd apt-get update
    $sudo_cmd apt-get install -y nvidia-container-toolkit
  fi
  info "Configuring Docker runtime for NVIDIA..."
  $sudo_cmd nvidia-ctk runtime configure --runtime=docker
  $sudo_cmd systemctl restart docker || $sudo_cmd service docker restart || true
}

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  install_docker
  if [[ -n "$DOCKER_DATA_ROOT" ]]; then
    configure_docker_data_root "$DOCKER_DATA_ROOT"
  fi
  install_nvidia_container_toolkit
else
  info "Skipping Docker install (--skip-docker)"
  if [[ -n "$DOCKER_DATA_ROOT" ]]; then
    configure_docker_data_root "$DOCKER_DATA_ROOT"
  fi
fi

IMAGE="${METHYL_PARABRICKS_IMAGE:-$(resolve_parabricks_image)}"
if [[ -z "$IMAGE" ]]; then
  warn "METHYL_PARABRICKS_IMAGE not set and platform matrix has no default"
else
  export METHYL_PARABRICKS_IMAGE="$IMAGE"
  info "Parabricks image: $METHYL_PARABRICKS_IMAGE"
  mkdir -p "$ENV_DIR"
  cat >"$ENV_DIR/parabricks.env" <<EOF
# Generated by scripts/setup_gpu_node.sh
METHYL_PARABRICKS_IMAGE=$METHYL_PARABRICKS_IMAGE
METHYL_PARABRICKS_GPU_FLAGS="--gpus all"
EOF
  info "Wrote $ENV_DIR/parabricks.env"
fi

# Optional proteomics GPU tool images (DIA-NN / Prosit / Casanovo). Non-Parabricks; each
# node advertises the matching capability only when its image env is set. On GH200/Grace
# (aarch64) these must be arm64/multi-arch images (verify before enabling).
DIANN_IMAGE="${METHYL_DIANN_IMAGE:-$(resolve_proteomics_image diann 2>/dev/null || true)}"
PROSIT_IMAGE="${METHYL_PROSIT_IMAGE:-$(resolve_proteomics_image prosit 2>/dev/null || true)}"
CASANOVO_IMAGE="${METHYL_CASANOVO_IMAGE:-$(resolve_proteomics_image casanovo 2>/dev/null || true)}"
SAGE_IMAGE="${METHYL_SAGE_IMAGE:-$(resolve_proteomics_image sage 2>/dev/null || true)}"
if [[ -n "$DIANN_IMAGE" || -n "$PROSIT_IMAGE" || -n "$CASANOVO_IMAGE" || -n "$SAGE_IMAGE" ]]; then
  mkdir -p "$ENV_DIR"
  {
    echo "# Generated by scripts/setup_gpu_node.sh (proteomics tools)"
    [[ -n "$DIANN_IMAGE" ]] && echo "METHYL_DIANN_IMAGE=$DIANN_IMAGE"
    [[ -n "$PROSIT_IMAGE" ]] && echo "METHYL_PROSIT_IMAGE=$PROSIT_IMAGE"
    [[ -n "$CASANOVO_IMAGE" ]] && echo "METHYL_CASANOVO_IMAGE=$CASANOVO_IMAGE"
    [[ -n "$SAGE_IMAGE" ]] && echo "METHYL_SAGE_IMAGE=$SAGE_IMAGE  # CPU DDA (Sage)"
  } >"$ENV_DIR/proteomics.env"
  info "Wrote $ENV_DIR/proteomics.env"
fi

if [[ "$PULL_PARABRICKS" -eq 1 ]]; then
  [[ -n "$IMAGE" ]] || die "Cannot pull Parabricks: METHYL_PARABRICKS_IMAGE unset"
  info "Pulling $IMAGE ..."
  docker pull "$IMAGE"
fi

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -x "$REPO_ROOT/scripts/verify_parabricks.sh" && -n "${IMAGE:-}" && "$PULL_PARABRICKS" -eq 1 ]]; then
  info "Running verify_parabricks.sh ..."
  export METHYL_PARABRICKS_IMAGE="$IMAGE"
  bash "$REPO_ROOT/scripts/verify_parabricks.sh" || warn "Parabricks verification failed (image may need NGC login)"
fi

info "GPU node setup complete."
