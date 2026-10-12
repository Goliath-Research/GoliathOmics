#!/usr/bin/env bash
# Initialize four-layer /work shared-storage roots and access modes.
#
# Worker-writable (0777 + default ACL when available):
#   samples/   — FASTQ/BAM/H5 scratch shared by every worker (incl. Docker root)
#   projects/  — study outputs
#   cache/     — mapper / enricher caches
#
# Worker-readable (0755, not other-writable):
#   genomes/   — reference inventory (ops / provision-assets write)
#   site/      — methyl_site.json (default written once; an existing file is kept)
#   goliath/ — releases, venvs, env, docker data-root (promote host writes)
#
# Creates missing roots only. Does not recurse into existing trees.
#
# Usage:
#   scripts/init_work_layout.sh [--work PATH] [--dry-run] [--skip-acl]
#
# Environment:
#   METHYL_WORK_ROOT / WORK_ROOT   Same as --work (default: /work)

set -euo pipefail

usage() {
  sed -n '2,20p' "$0"
}

WORK="${METHYL_WORK_ROOT:-${WORK_ROOT:-/work}}"
DRY_RUN=0
SKIP_ACL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --work) WORK="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-acl) SKIP_ACL=1; shift ;;
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

# 0777 — any worker uid (and Docker-as-root) can create sample / study / cache dirs.
WRITABLE_MODE=0777
# 0755 — workers read; owner (ops / promote) writes. Not world-writable.
READONLY_MODE=0755

WRITABLE_DIRS=(samples projects cache)
READONLY_DIRS=(genomes site goliath)

ensure_dir() {
  local path="$1"
  local mode="$2"
  if [[ ! -d "$path" ]]; then
    run mkdir -p "$path"
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] chmod $mode $path"
    return 0
  fi
  if ! chmod "$mode" "$path" 2>/dev/null; then
    warn "Could not chmod $mode $path (not owner?); current $(stat -c '%a' "$path" 2>/dev/null || echo unknown)"
  fi
}

apply_default_acl() {
  local path="$1"
  if [[ "$SKIP_ACL" -eq 1 ]]; then
    return 0
  fi
  if ! command -v setfacl >/dev/null 2>&1; then
    warn "setfacl not found — Docker-created children under $path may not inherit other-write"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] setfacl -m o::rwx -d -m u::rwx -d -m g::rwx -d -m o::rwx $path"
    return 0
  fi
  # Default ACL so Parabricks/Docker (root) children stay writable by every worker.
  if ! setfacl -m o::rwx -d -m u::rwx -d -m g::rwx -d -m o::rwx "$path" 2>/dev/null; then
    warn "setfacl failed on $path (NFS export may lack ACL); root stays mode $(stat -c '%a' "$path" 2>/dev/null || echo unknown)"
  fi
}

[[ -n "$WORK" ]] || die "--work is empty"
if [[ ! -d "$WORK" ]]; then
  die "Work mount missing: $WORK (ops must mount shared storage before init)"
fi

info "Init work layout"
info "  work: $WORK"

for name in "${WRITABLE_DIRS[@]}"; do
  ensure_dir "$WORK/$name" "$WRITABLE_MODE"
  apply_default_acl "$WORK/$name"
done

for name in "${READONLY_DIRS[@]}"; do
  ensure_dir "$WORK/$name" "$READONLY_MODE"
done

SITE_JSON="$WORK/site/methyl_site.json"
DEFAULT_SITE="$(cd "$(dirname "$0")" && pwd)/default_methyl_site.json"
if [[ ! -f "$DEFAULT_SITE" ]]; then
  die "Default site manifest missing: $DEFAULT_SITE"
fi
if [[ -f "$SITE_JSON" ]]; then
  info "Site manifest already present, left unchanged: $SITE_JSON"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] write default $SITE_JSON from $DEFAULT_SITE"
else
  sed "s|__WORK__|$WORK|g" "$DEFAULT_SITE" > "$SITE_JSON"
  chmod 0644 "$SITE_JSON"
  info "Wrote default site manifest $SITE_JSON (edit pins before provisioning genomes)"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  for name in "${WRITABLE_DIRS[@]}"; do
    mode="$(stat -c '%a' "$WORK/$name" 2>/dev/null || echo '?')"
    info "  $name/ mode=$mode (worker-writable)"
  done
  for name in "${READONLY_DIRS[@]}"; do
    mode="$(stat -c '%a' "$WORK/$name" 2>/dev/null || echo '?')"
    info "  $name/ mode=$mode (worker-readable)"
  done
fi

info "Work layout initialized"
