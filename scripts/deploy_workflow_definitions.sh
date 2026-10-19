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
  --keep-instances     On replace, do not delete running instances (default)
  --delete-instances   On replace, delete instances (destructive)
  -h, --help           Show this help

Requires BACKEND_DB + AZURE_SQL_* or POSTGRES_* (see deploy/env/gateway.*.env.example).
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

# On a GitHub runner there is no /work. Prefer GOLIATH_ENV_DIR, else a writable
# temp dir. Workers resolve SamplePrepPipeline from the DB (program_path /
# get_workflow_definition_by_name); the JSON is for canary/smoke convenience and
# is uploaded as a release-deploy artifact when produced on the runner.
if [[ -n "${GOLIATH_ENV_DIR:-}" ]]; then
  _goliath_env="$GOLIATH_ENV_DIR"
elif [[ -d /work/goliath ]]; then
  _goliath_env="/work/goliath/env"
else
  _goliath_env="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/goliath-env"
fi
OUTPUT_DIR="${_goliath_env}/compiled"
VERSIONS_OUT="${_goliath_env}/workflow_versions.json"
DRY_RUN=0
KEEP_INSTANCES="${GOLIATH_DEPLOY_KEEP_INSTANCES:-1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base) echo "Note: --api-base ignored (direct DB deploy only)" >&2; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --versions-out) VERSIONS_OUT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --keep-instances) KEEP_INSTANCES=1; shift ;;
    --delete-instances) KEEP_INSTANCES=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
fi

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

export GOLIATH_DEPLOY_KEEP_INSTANCES="$KEEP_INSTANCES"
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
# Methyl SamplePrep trio is required for catalog_sync_release. RNA/proteomics
# packs are compiled when fixtures exist but may reference actions not yet
# seeded in wf.workflow_action — soft-fail those unless forced.
_REQUIRED_STEMS = {
    "sample_prep_compiled",
    "sample_prep_remediate_compiled",
    "study_validation_lifecycle_compiled",
}
import os as _os
_force_optional = _os.environ.get("GOLIATH_DEPLOY_OPTIONAL_PACKS", "").lower() in (
    "1", "true", "yes",
)
try:
    paths = sorted(out_dir.glob("*_compiled.json"))
    required = [p for p in paths if p.stem in _REQUIRED_STEMS]
    optional = [p for p in paths if p.stem not in _REQUIRED_STEMS]
    for path in required + optional:
        spec = json.loads(path.read_text(encoding="utf-8"))
        name = spec.get("name") or path.stem
        delete_instances = False
        if _os.environ.get("GOLIATH_DEPLOY_KEEP_INSTANCES", "1") in ("0", "false", "False"):
            delete_instances = True
        optional_pack = path.stem not in _REQUIRED_STEMS
        try:
            result = deploy_workflow_definition(
                db,
                {"spec": spec, "replace": True, "delete_instances": delete_instances},
                create_workflow_definition=create_workflow_definition,
                delete_workflow_definition=delete_workflow_definition,
            )
        except Exception as exc:
            if optional_pack and not _force_optional:
                print(
                    f"WARN: optional pack {name} ({path.name}) skipped: {exc}",
                    file=sys.stderr,
                )
                results[name] = {"skipped": True, "error": str(exc)}
                continue
            raise
        results[name] = result
        print(f"deployed {name}: workflow_version_id={result.get('workflow_version_id')}")
    missing = _REQUIRED_STEMS - {p.stem for p in required}
    if missing:
        raise SystemExit(f"required compiled specs missing: {sorted(missing)}")
finally:
    db.close()

versions_out.parent.mkdir(parents=True, exist_ok=True)
versions_out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
print(f"wrote {versions_out}")
PY
