#!/usr/bin/env bash
# Compile and deploy algorithm-generic MC / lifecycle / SaMD DomainPrograms.
#
# Usage:
#   source .venv/bin/activate
#   export BACKEND_DB=mssql   # or postgres
#   # set AZURE_SQL_* or POSTGRES_* (see deploy/env/gateway.*.env.example)
#   bash scripts/deploy_mc_workflow_definitions.sh
#
# Options:
#   --dry-run          Compile only
#   --delete-instances Pass delete_instances=true on replace deploy (default: false)
#   -h, --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

DRY_RUN=0
DELETE_INSTANCES=0

usage() {
  sed -n '2,16p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base|--use-gateway)
      echo "Note: gateway deploy removed; using direct DB only ($1 ignored)" >&2
      if [[ "$1" == "--api-base" ]]; then shift 2; else shift; fi
      ;;
    --dry-run) DRY_RUN=1; shift ;;
    --delete-instances) DELETE_INSTANCES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN=python3

# Prefer runtime-bundle fixtures when present (no-git workers / operators).
# Print the release directory ROOT/current points at, or nothing (exit 1)
# when it is missing, empty or invalid. Shared /work volumes cannot store
# symlinks (ln -s left a 0-byte regular file), so current is a pointer file
# holding the release path; a symlink or a real directory (older layouts) is
# still accepted.
goliath_current_dir() {
  local root="${1%/}" cur target=""
  cur="$root/current"
  if [[ -d "$cur" ]]; then
    target="$(cd "$cur" 2>/dev/null && pwd -P)" || return 1
  elif [[ -f "$cur" && -s "$cur" ]]; then
    target="$(head -n 1 "$cur" 2>/dev/null | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$target" || "$target" == /* ]] || target="$root/$target"
  fi
  [[ -n "$target" && -d "$target" ]] || return 1
  printf '%s\n' "$target"
}
_cur="$(goliath_current_dir "${GOLIATH_ROOT:-/work/goliath}" 2>/dev/null || echo "${GOLIATH_ROOT:-/work/goliath}/current")"
RUNTIME_DOMAIN="${METHYL_RUNTIME_ROOT:-$_cur/runtime-bundle}/domain"
if [[ -d "$RUNTIME_DOMAIN/fixtures" ]]; then
  DOMAIN_ROOT="$RUNTIME_DOMAIN"
else
  DOMAIN_ROOT="$REPO_ROOT/workflow_engine/domain"
fi

COMPILE="$REPO_ROOT/scripts/compile_workflow_program.py"
OUT_ROOT="${GOLIATH_ENV_DIR:-/work/goliath/env}/compiled/mc"
VERSIONS_OUT="${GOLIATH_ENV_DIR:-/work/goliath/env}/workflow_versions_mc.json"

PROGRAMS=(
  "fixtures/mc_stability.program.json"
  "fixtures/mc_stability_residual.program.json"
  "fixtures/mc_stability_staged.program.json"
  "fixtures/mc_stability_smoke.program.json"
  "fixtures/mc_stability_ppi.program.json"
  "fixtures/mc_gene_enricher_stability.program.json"
  "fixtures/study_validation_lifecycle.program.json"
  "fixtures/study_validation_lifecycle_residual.program.json"
  "fixtures/full_lifecycle.program.json"
  "fixtures/validation_freeze.program.json"
  "fixtures/validation_model.program.json"
  "fixtures/data_driven.program.json"
  "fixtures/interpretation.program.json"
  "fixtures/legacy_dual.program.json"
  "fixtures/samd_research.program.json"
  "fixtures/samd_holdout_enrichment.program.json"
  "fixtures/samd_pivotal.program.json"
)

mkdir -p "$OUT_ROOT"
COMPILED=()
for rel in "${PROGRAMS[@]}"; do
  src="$DOMAIN_ROOT/$rel"
  [[ -f "$src" ]] || src="$REPO_ROOT/workflow_engine/domain/$rel"
  [[ -f "$src" ]] || { echo "Missing $rel (looked under $DOMAIN_ROOT and repo)" >&2; exit 1; }
  stem="$(basename "$rel" .program.json)"
  out="$OUT_ROOT/${stem}_compiled.json"
  "$PYTHON_BIN" "$COMPILE" "$src" -o "$out"
  COMPILED+=("$out")
  echo "compiled: $out"
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry-run: specs in $OUT_ROOT"
  exit 0
fi

rollouts_begin workflow-definitions "mc workflow definitions ($(hostname -s 2>/dev/null || hostname))"

"$PYTHON_BIN" - "$REPO_ROOT" "$DELETE_INSTANCES" "$VERSIONS_OUT" "${COMPILED[@]}" <<'PY'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
delete_instances = sys.argv[2] == "1"
versions_out = Path(sys.argv[3])
compiled_paths = [Path(p) for p in sys.argv[4:]]

sys.path.insert(0, str(repo / "workflow_engine"))
from ops.workflow_deploy import deploy_workflow_definition
from rest.connection import resolve_connection_config
from rest.db import open_gateway_db
from rest.db_client import create_workflow_definition, delete_workflow_definition

config = resolve_connection_config()
db = open_gateway_db(config)
results = {}
try:
    for path in compiled_paths:
        spec = json.loads(path.read_text(encoding="utf-8"))
        result = deploy_workflow_definition(
            db,
            {"spec": spec, "replace": True, "delete_instances": delete_instances},
            create_workflow_definition=create_workflow_definition,
            delete_workflow_definition=delete_workflow_definition,
        )
        name = spec.get("name") or path.stem
        results[name] = result
        print(f"deployed {name}: workflow_version_id={result.get('workflow_version_id')}")
finally:
    db.close()

versions_out.parent.mkdir(parents=True, exist_ok=True)
versions_out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
print(f"wrote {versions_out}")
PY
