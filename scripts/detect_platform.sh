#!/bin/bash
# Detect host architecture and load platform_matrix.env defaults.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATRIX_FILE="${PLATFORM_MATRIX_FILE:-$SCRIPT_DIR/platform_matrix.env}"

detect_uname_arch() {
  uname -m
}

# Map uname -m to matrix key (aarch64 | amd64)
platform_arch_key() {
  local uname_arch="${1:-$(detect_uname_arch)}"
  case "$uname_arch" in
    aarch64|arm64) echo "aarch64" ;;
    x86_64|amd64) echo "amd64" ;;
    *) echo "unknown" ;;
  esac
}

# Source platform_matrix.env and resolve PARABRICKS_IMAGE for current host.
resolve_parabricks_image() {
  local arch_key
  arch_key="$(platform_arch_key)"
  if [[ -f "$MATRIX_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$MATRIX_FILE"
  fi
  local var="PARABRICKS_IMAGE_${arch_key}"
  echo "${!var:-}"
}

# Resolve a proteomics GPU tool image (diann|prosit|casanovo) for current host arch.
resolve_proteomics_image() {
  local tool="${1:?tool required (diann|prosit|casanovo)}"
  local arch_key
  arch_key="$(platform_arch_key)"
  if [[ -f "$MATRIX_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$MATRIX_FILE"
  fi
  local upper
  upper="$(echo "$tool" | tr '[:lower:]' '[:upper:]')"
  local var="PROTEOMICS_${upper}_IMAGE_${arch_key}"
  echo "${!var:-}"
}

# Resolve methylGrapher(+vg) worker image for current host arch.
resolve_methylgrapher_image() {
  local arch_key
  arch_key="$(platform_arch_key)"
  if [[ -f "$MATRIX_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$MATRIX_FILE"
  fi
  local var="METHYLGRAPHER_IMAGE_${arch_key}"
  echo "${METHYL_METHYLGRAPHER_IMAGE:-${!var:-}}"
}

resolve_methyl_extractor_subdir() {
  local uname_arch="${1:-$(detect_uname_arch)}"
  if [[ -f "$MATRIX_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$MATRIX_FILE"
  fi
  case "$uname_arch" in
    aarch64|arm64) echo "${METHYL_EXTRACTOR_ARCH_aarch64:-arm64}" ;;
    x86_64|amd64) echo "${METHYL_EXTRACTOR_ARCH_x86_64:-x64}" ;;
    *) return 1 ;;
  esac
}

# Build a literal PATH for worker.env / systemd (no $PATH suffix — EnvironmentFile does not expand).
expand_worker_path() {
  local venv_dir="${1:?venv directory required}"
  local fallback="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  local venv_bin
  if command -v realpath >/dev/null 2>&1; then
    venv_bin="$(realpath "$venv_dir")/bin"
  else
    venv_bin="$(cd "$venv_dir" && pwd)/bin"
  fi
  local base="${PATH:-$fallback}"
  local -a parts=() kept=() p
  local IFS=:
  read -ra parts <<< "$base"
  for p in "${parts[@]}"; do
    [[ -z "$p" ]] && continue
    [[ "$p" == "$venv_bin" ]] && continue
    kept+=("$p")
  done
  if ((${#kept[@]} == 0)); then
    base="$fallback"
  else
    IFS=:
    base="${kept[*]}"
  fi
  echo "${venv_bin}:${base}"
}

# Strip optional leading "v" from git tags (v2026.6.1 -> 2026.6.1).
normalize_release_version() {
  local ver="${1:?version required}"
  ver="${ver#v}"
  echo "$ver"
}

# SemVer 2.0 without build metadata (+suffix). No leading zeros (Azure Universal Packages).
validate_release_version() {
  local ver="${1:?version required}"
  [[ "$ver" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[a-z0-9]+([-.][a-z0-9]+)*)?$ ]]
}

require_release_version() {
  local ver="$1"
  local ctx="${2:-release version}"
  if ! validate_release_version "$ver"; then
    echo "[ERROR] Invalid $ctx: '$ver'" >&2
    echo "[ERROR] Use SemVer 2.0 without leading zeros (e.g. 2026.6.1, not 2026.06.1)." >&2
    echo "[ERROR] Required for Azure Universal Packages and manifest.json." >&2
    return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "uname: $(detect_uname_arch)"
  echo "arch_key: $(platform_arch_key)"
  echo "parabricks_image: $(resolve_parabricks_image)"
  echo "methyl_extractor_subdir: $(resolve_methyl_extractor_subdir)"
fi
