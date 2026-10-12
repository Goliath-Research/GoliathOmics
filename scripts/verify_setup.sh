#!/usr/bin/env bash
# Check that a release tree or the promoted current/ tree has the runtime bundle.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/verify_setup.sh [options]

Options:
  --root PATH          Goliath root (default: /work/goliath)
  --runtime PATH       runtime-bundle directory (default: <root>/current/runtime-bundle)
  --runtime-bundle     Check this script's sibling runtime scripts only
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${GOLIATH_ROOT:-/work/goliath}"
RUNTIME=""
BUNDLE_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --runtime) RUNTIME="${2:-}"; shift 2 ;;
    --runtime-bundle) BUNDLE_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

if [[ "$BUNDLE_ONLY" -eq 1 ]]; then
  RUNTIME="$SCRIPT_DIR"
fi
if [[ -z "$RUNTIME" && -d "$ROOT/current/runtime-bundle/scripts" ]]; then
  RUNTIME="$ROOT/current/runtime-bundle/scripts"
fi
if [[ -z "$RUNTIME" ]]; then
  RUNTIME="$SCRIPT_DIR"
fi

required=(detect_platform.sh platform_matrix.env promote_release.sh verify_setup.sh)
for name in "${required[@]}"; do
  [[ -f "$RUNTIME/$name" || -f "$SCRIPT_DIR/$name" ]] || die "Missing runtime script: $name (looked in $RUNTIME)"
done

if [[ "$BUNDLE_ONLY" -eq 0 ]]; then
  manifest="$ROOT/current/manifest.json"
  if [[ -f "$manifest" ]]; then
    info "manifest: $manifest"
  else
    info "No $manifest yet (expected before the first promote)"
  fi
fi

info "verify_setup passed ($RUNTIME)"
