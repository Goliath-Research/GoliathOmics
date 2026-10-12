#!/usr/bin/env bash
# Deploy Monte Carlo / multi-comparison DomainPrograms.
# The child deploy_workflow_definitions.sh is told not to open its own
# Rollouts deployment; this script reports service workflow-definitions.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/deploy_mc_workflow_definitions.sh [options]

Options:
  --dry-run
  --replace
  --output DIR
  -h, --help

Selects workflow_engine/domain/fixtures/mc_*.program.json and
validation_model_mc*.program.json.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

DRY=()
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY+=(--dry-run); shift ;;
    --replace) EXTRA+=(--replace); shift ;;
    --output) EXTRA+=(--output "${2:-}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

shopt -s nullglob
programs=(
  "$REPO_ROOT"/workflow_engine/domain/fixtures/mc_*.program.json
  "$REPO_ROOT"/workflow_engine/domain/fixtures/validation_model_mc*.program.json
)
shopt -u nullglob
[[ ${#programs[@]} -gt 0 ]] || {
  echo "No MC DomainPrograms found" >&2
  exit 1
}

if [[ ${#DRY[@]} -eq 0 ]]; then
  rollouts_begin workflow-definitions "mc workflow definitions ($(hostname -s 2>/dev/null || hostname))"
fi

export GOLIATH_ROLLOUTS_EXTERNAL=1
bash "$SCRIPT_DIR/deploy_workflow_definitions.sh" "${DRY[@]}" "${EXTRA[@]}" "${programs[@]}"
