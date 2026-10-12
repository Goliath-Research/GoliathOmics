#!/usr/bin/env bash
# Compile DomainPrograms and upsert workflow definitions when the platform DB
# client (GoliathApp rest.db_client) is installed. Compiled specs are always
# written under the output directory.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/deploy_workflow_definitions.sh [options] [program.json ...]

Options:
  --program PATH     DomainProgram JSON (repeatable). Default: SamplePrep +
                     study validation lifecycle fixtures.
  --output DIR       Where compiled specs are written
                     (default: <repo>/build/workflow-definitions)
  --replace          Pass replace=true to the platform upsert
  --dry-run          List programs and exit without a database connection
  -h, --help

Env: BACKEND_DB and POSTGRES_* or AZURE_SQL_* (same as the gateway).
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

OUTPUT="$REPO_ROOT/build/workflow-definitions"
REPLACE=0
DRY_RUN=0
PROGRAMS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --program) PROGRAMS+=("${2:-}"); shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      PROGRAMS+=("$1")
      shift
      ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

if [[ ${#PROGRAMS[@]} -eq 0 ]]; then
  PROGRAMS=(
    "$REPO_ROOT/workflow_engine/domain/fixtures/sample_prep.program.json"
    "$REPO_ROOT/workflow_engine/domain/fixtures/study_validation_lifecycle.program.json"
  )
fi

for program in "${PROGRAMS[@]}"; do
  [[ -f "$program" ]] || die "Program not found: $program"
  info "program: $program"
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  info "dry-run: not contacting the database"
  exit 0
fi

rollouts_begin workflow-definitions "workflow definitions ($(hostname -s 2>/dev/null || hostname))"

mkdir -p "$OUTPUT"
export GOLIATH_PROGRAMS
GOLIATH_PROGRAMS="$(printf '%s\n' "${PROGRAMS[@]}")"
export GOLIATH_WF_OUTPUT="$OUTPUT"
export GOLIATH_WF_REPLACE="$REPLACE"
export GOLIATH_REPO_ROOT="$REPO_ROOT"

python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

repo = Path(os.environ["GOLIATH_REPO_ROOT"])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "workflow_engine"))
sys.path.insert(0, str(repo / "workflow_engine" / "domain"))
sys.path.insert(0, str(repo / "packages" / "methyldomain"))

from ops.study_lifecycle import compile_program_spec

programs = [Path(line) for line in os.environ["GOLIATH_PROGRAMS"].splitlines() if line.strip()]
out = Path(os.environ["GOLIATH_WF_OUTPUT"])
replace = os.environ.get("GOLIATH_WF_REPLACE") == "1"
specs = []
for program in programs:
    spec = compile_program_spec(program)
    dest = out / (program.stem + ".spec.json")
    dest.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"compiled {program.name} -> {dest}")
    specs.append(spec)

try:
    from rest.connection import resolve_connection_config
    from rest.db import open_gateway_db
    from rest.db_client import create_workflow_definition
except ImportError as exc:
    print(
        "Platform DB client is not installed (rest.db_client). "
        f"Specs were written under {out}. Import error: {exc}",
        file=sys.stderr,
    )
    sys.exit(1)

db = open_gateway_db(resolve_connection_config())
try:
    versions = []
    for spec in specs:
        if replace:
            spec = dict(spec)
        created = create_workflow_definition(db, spec)
        versions.append(
            {
                "name": spec.get("name"),
                "workflow_version_id": created.get("workflow_version_id"),
            }
        )
        print(f"upserted {spec.get('name')} version={created.get('workflow_version_id')}")
finally:
    db.close()

(out / "workflow_versions.json").write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")
PY

info "Workflow definitions deployed"
