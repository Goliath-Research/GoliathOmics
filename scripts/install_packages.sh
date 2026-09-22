#!/bin/bash
# Install MethylPipeline packages from scripts/packages.list (editable mode).
# Also installs workers/ when present. Sourced by setup_host.sh and install_all.sh.
#
# With --with-deps, pip may materialize path dependencies as non-editable
# site-packages copies. After the list (and workers), foundational packages are
# re-installed editable --no-deps so repo schemas/source stay reachable.

set -euo pipefail

# Path deps often get overwritten as plain wheels during --with-deps; keep these editable.
FOUNDATIONAL_PACKAGES=(methylutils methyldomain)

_assert_editable_foundation() {
  local python_bin="$1"
  local dist_name="$2"
  local show
  show="$("$python_bin" -m pip show "$dist_name" 2>/dev/null || true)"
  if [[ -z "$show" ]]; then
    echo "[ERROR] Expected distribution not installed: $dist_name" >&2
    return 1
  fi
  if ! grep -q '^Editable project location:' <<<"$show"; then
    echo "[ERROR] $dist_name is not an editable install (pip --with-deps overwrote it)." >&2
    echo "$show" >&2
    return 1
  fi
}

_reassert_foundational_editable() {
  local project_root="$1"
  local python_bin="$2"
  local packages_dir="$3"
  local pkg pkg_path dist_name

  for pkg in "${FOUNDATIONAL_PACKAGES[@]}"; do
    pkg_path="$packages_dir/$pkg"
    if [[ ! -d "$pkg_path" ]]; then
      echo "[WARN] Foundational package missing: $pkg_path" >&2
      continue
    fi
    echo "[INFO] Re-asserting editable install: $pkg"
    "$python_bin" -m pip install -e "$pkg_path" --no-deps
  done

  if [[ "${METHYL_REQUIRE_EDITABLE_FOUNDATIONS:-1}" == "1" ]]; then
    # pip show uses distribution names from pyproject (methyldomain -> methyl_domain).
    for dist_name in methylutils methyl_domain; do
      _assert_editable_foundation "$python_bin" "$dist_name"
    done
  fi
}

install_packages_from_list() {
  local project_root="${1:?project root required}"
  local python_bin="${2:?python binary required}"
  local packages_dir="${3:-$project_root/packages}"
  local with_deps="${4:-0}"
  local list_file="${5:-$project_root/scripts/packages.list}"

  local pip_flags=(--no-deps)
  if [[ "$with_deps" -eq 1 ]]; then
    pip_flags=()
  fi

  if [[ ! -f "$list_file" ]]; then
    echo "[ERROR] Package list not found: $list_file" >&2
    return 1
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(echo "$line" | xargs)"
    [[ -z "$line" ]] && continue

    local pkg_path="$packages_dir/$line"
    if [[ ! -d "$pkg_path" ]]; then
      # Some listed packages (e.g. workflow_engine) live at the repo root, not under packages/.
      if [[ -d "$project_root/$line" ]]; then
        pkg_path="$project_root/$line"
      else
        echo "[WARN] Skipping $line (directory not found: $packages_dir/$line or $project_root/$line)"
        continue
      fi
    fi
    if [[ ! -f "$pkg_path/pyproject.toml" && ! -f "$pkg_path/setup.py" ]]; then
      echo "[WARN] Skipping $line (missing pyproject.toml/setup.py)"
      continue
    fi

    echo "[INFO] Installing $line..."
    "$python_bin" -m pip install -e "$pkg_path" "${pip_flags[@]}"
  done < "$list_file"

  local workers_path="$project_root/workers"
  if [[ -d "$workers_path" && -f "$workers_path/pyproject.toml" ]]; then
    echo "[INFO] Installing workers..."
    "$python_bin" -m pip install -e "$workers_path" "${pip_flags[@]}"
  else
    echo "[WARN] workers/ not found or missing pyproject.toml"
  fi

  # Always re-editable foundations after the list/workers pass so --with-deps
  # path resolution cannot leave stale site-packages copies.
  _reassert_foundational_editable "$project_root" "$python_bin" "$packages_dir"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  fi
  WITH_DEPS=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --with-deps) WITH_DEPS=1; shift ;;
      -h|--help)
        echo "Usage: scripts/install_packages.sh [--with-deps]"
        exit 0
        ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done
  install_packages_from_list "$PROJECT_ROOT" "$PYTHON_BIN" "$PROJECT_ROOT/packages" "$WITH_DEPS"
fi
