#!/bin/bash
# Compile SamplePrep + StudyValidationLifecycle DomainPrograms and deploy via direct DB.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/deploy_workflow_definitions.sh [options]

Options:
  --output-dir PATH    Write compiled specs (default: /work/goliath/env/compiled)
  --versions-out PATH  workflow_version_id map (default: /work/goliath/env/workflow_versions.json)
  --dry-run            Compile only; do not deploy
  -h, --help           Show this help

Requires BACKEND_DB + AZURE_SQL_* or POSTGRES_* (see deploy/env/gateway.*.env.example).
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

OUTPUT_DIR="${GOLIATH_ENV_DIR:-/work/goliath/env}/compiled"
VERSIONS_OUT="${GOLIATH_ENV_DIR:-/work/goliath/env}/workflow_versions.json"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base) echo "Note: --api-base ignored (direct DB deploy only)" >&2; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --versions-out) VERSIONS_OUT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
fi

RUNTIME_DOMAIN="${METHYL_RUNTIME_ROOT:-${GOLIATH_ROOT:-/work/goliath}/current/runtime-bundle}/domain"
REPO_FIXTURES="$REPO_ROOT/workflow_engine/domain/fixtures"
if [[ -f "$RUNTIME_DOMAIN/fixtures/sample_prep.program.json" ]]; then
  FIXTURES="$RUNTIME_DOMAIN/fixtures"
else
  FIXTURES="$REPO_FIXTURES"
fi
SAMPLE_PREP="$FIXTURES/sample_prep.program.json"
REMEDIATE="$FIXTURES/sample_prep_remediate.program.json"
LIFECYCLE="$FIXTURES/study_validation_lifecycle.program.json"
RNA_PREP="$FIXTURES/sample_prep_rnaseq.program.json"
RNA_LIFECYCLE="$FIXTURES/rnaseq_study_lifecycle.program.json"
PROT_PREP="$FIXTURES/sample_prep_proteomics.program.json"
PROT_LIFECYCLE="$FIXTURES/proteomics_study_lifecycle.program.json"
[[ -f "$LIFECYCLE" ]] || LIFECYCLE="$REPO_FIXTURES/study_validation_lifecycle.program.json"
[[ -f "$SAMPLE_PREP" ]] || SAMPLE_PREP="$REPO_FIXTURES/sample_prep.program.json"
[[ -f "$REMEDIATE" ]] || REMEDIATE="$REPO_FIXTURES/sample_prep_remediate.program.json"
[[ -f "$RNA_PREP" ]] || RNA_PREP="$REPO_FIXTURES/sample_prep_rnaseq.program.json"
[[ -f "$RNA_LIFECYCLE" ]] || RNA_LIFECYCLE="$REPO_FIXTURES/rnaseq_study_lifecycle.program.json"
[[ -f "$PROT_PREP" ]] || PROT_PREP="$REPO_FIXTURES/sample_prep_proteomics.program.json"
[[ -f "$PROT_LIFECYCLE" ]] || PROT_LIFECYCLE="$REPO_FIXTURES/proteomics_study_lifecycle.program.json"
for f in "$SAMPLE_PREP" "$REMEDIATE" "$LIFECYCLE"; do
  [[ -f "$f" ]] || { echo "Missing $f" >&2; exit 1; }
done

mkdir -p "$OUTPUT_DIR" "$(dirname "$VERSIONS_OUT")"

COMPILE="$REPO_ROOT/scripts/compile_workflow_program.py"
"$PYTHON_BIN" "$COMPILE" "$SAMPLE_PREP" -o "$OUTPUT_DIR/sample_prep_compiled.json"
"$PYTHON_BIN" "$COMPILE" "$REMEDIATE" -o "$OUTPUT_DIR/sample_prep_remediate_compiled.json"
"$PYTHON_BIN" "$COMPILE" "$LIFECYCLE" -o "$OUTPUT_DIR/study_validation_lifecycle_compiled.json"
# RNA-Seq process pack (optional; compiled when fixtures are present).
if [[ -f "$RNA_PREP" ]]; then
  "$PYTHON_BIN" "$COMPILE" "$RNA_PREP" -o "$OUTPUT_DIR/sample_prep_rnaseq_compiled.json"
fi
if [[ -f "$RNA_LIFECYCLE" ]]; then
  "$PYTHON_BIN" "$COMPILE" "$RNA_LIFECYCLE" -o "$OUTPUT_DIR/rnaseq_study_lifecycle_compiled.json"
fi
if [[ -f "$PROT_PREP" ]]; then
  "$PYTHON_BIN" "$COMPILE" "$PROT_PREP" -o "$OUTPUT_DIR/sample_prep_proteomics_compiled.json"
fi
if [[ -f "$PROT_LIFECYCLE" ]]; then
  "$PYTHON_BIN" "$COMPILE" "$PROT_LIFECYCLE" -o "$OUTPUT_DIR/proteomics_study_lifecycle_compiled.json"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry-run: compiled specs in $OUTPUT_DIR"
  exit 0
fi

rollouts_begin workflow-definitions "workflow definitions ($(hostname -s 2>/dev/null || hostname))"

"$PYTHON_BIN" - "$REPO_ROOT" "$OUTPUT_DIR" "$VERSIONS_OUT" <<'PY'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
out_dir = Path(sys.argv[2])
versions_out = Path(sys.argv[3])
sys.path.insert(0, str(repo / "workflow_engine"))

from ops.workflow_deploy import deploy_workflow_definition
from rest.connection import resolve_connection_config
from rest.db import open_gateway_db
from rest.db_client import create_workflow_definition, delete_workflow_definition

config = resolve_connection_config()
db = open_gateway_db(config)
results = {}
try:
    for path in sorted(out_dir.glob("*_compiled.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        name = spec.get("name") or path.stem
        result = deploy_workflow_definition(
            db,
            {"spec": spec, "replace": True, "delete_instances": True},
            create_workflow_definition=create_workflow_definition,
            delete_workflow_definition=delete_workflow_definition,
        )
        results[name] = result
        print(f"deployed {name}: workflow_version_id={result.get('workflow_version_id')}")
finally:
    db.close()

versions_out.parent.mkdir(parents=True, exist_ok=True)
versions_out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
print(f"wrote {versions_out}")
PY
