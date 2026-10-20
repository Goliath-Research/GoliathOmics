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

# Fixtures travel with this checkout or this release. Do not follow
# /work/goliath/current: that pointer still names the previous release
# until promote, and compiling it deploys a stale program.
if [[ -d "$REPO_ROOT/workflow_engine/domain/fixtures" ]]; then
  FIXTURES="$REPO_ROOT/workflow_engine/domain/fixtures"
elif [[ -d "$REPO_ROOT/domain/fixtures" ]]; then
  FIXTURES="$REPO_ROOT/domain/fixtures"
elif [[ -d "$REPO_ROOT/runtime-bundle/domain/fixtures" ]]; then
  FIXTURES="$REPO_ROOT/runtime-bundle/domain/fixtures"
else
  echo "DomainProgram fixtures not found under $REPO_ROOT" >&2
  exit 1
fi
REPO_FIXTURES="$FIXTURES"
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
if [[ ! -f "$COMPILE" && -f "$REPO_ROOT/runtime-bundle/scripts/compile_workflow_program.py" ]]; then
  COMPILE="$REPO_ROOT/runtime-bundle/scripts/compile_workflow_program.py"
fi
SOURCES_TSV="$OUTPUT_DIR/program_sources.tsv"
: >"$SOURCES_TSV"
compile_one() {
  local src="$1" dest="$2"
  "$PYTHON_BIN" "$COMPILE" "$src" -o "$dest"
  printf '%s\t%s\n' "$dest" "$src" >>"$SOURCES_TSV"
}
compile_one "$SAMPLE_PREP" "$OUTPUT_DIR/sample_prep_compiled.json"
compile_one "$REMEDIATE" "$OUTPUT_DIR/sample_prep_remediate_compiled.json"
compile_one "$LIFECYCLE" "$OUTPUT_DIR/study_validation_lifecycle_compiled.json"
# RNA-Seq process pack (optional; compiled when fixtures are present).
if [[ -f "$RNA_PREP" ]]; then
  compile_one "$RNA_PREP" "$OUTPUT_DIR/sample_prep_rnaseq_compiled.json"
fi
if [[ -f "$RNA_LIFECYCLE" ]]; then
  compile_one "$RNA_LIFECYCLE" "$OUTPUT_DIR/rnaseq_study_lifecycle_compiled.json"
fi
if [[ -f "$PROT_PREP" ]]; then
  compile_one "$PROT_PREP" "$OUTPUT_DIR/sample_prep_proteomics_compiled.json"
fi
if [[ -f "$PROT_LIFECYCLE" ]]; then
  compile_one "$PROT_LIFECYCLE" "$OUTPUT_DIR/proteomics_study_lifecycle_compiled.json"
fi

CATALOG_JSON=""
for candidate in \
  "$REPO_ROOT/schemas/actions/catalog.json" \
  "$REPO_ROOT/runtime-bundle/schemas/actions/catalog.json"
do
  if [[ -f "$candidate" ]]; then
    CATALOG_JSON="$candidate"
    break
  fi
done
if [[ -z "$CATALOG_JSON" ]]; then
  echo "Action catalog not found under $REPO_ROOT/schemas/actions/catalog.json" >&2
  exit 1
fi
export CATALOG_JSON SOURCES_TSV

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry-run: compiled specs in $OUTPUT_DIR"
  exit 0
fi

rollouts_begin workflow-definitions "workflow definitions ($(hostname -s 2>/dev/null || hostname))"

export GOLIATH_DEPLOY_KEEP_INSTANCES="$KEEP_INSTANCES"
"$PYTHON_BIN" - "$REPO_ROOT" "$OUTPUT_DIR" "$VERSIONS_OUT" <<'PY'
import hashlib
import json
import os
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

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


catalog_sha = _sha256(Path(os.environ["CATALOG_JSON"]))
sources: dict[str, Path] = {}
for line in Path(os.environ["SOURCES_TSV"]).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    dest, src = line.split("\t", 1)
    sources[Path(dest).name] = Path(src)

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
        recorded = dict(result)
        program_path = sources.get(path.name)
        if program_path is not None:
            recorded["program_sha256"] = _sha256(program_path)
        recorded["catalog_sha256"] = catalog_sha
        results[name] = recorded
        print(
            f"deployed {name}: workflow_version_id={recorded.get('workflow_version_id')} "
            f"program_sha256={recorded.get('program_sha256', '')} "
            f"catalog_sha256={catalog_sha}"
        )
    missing = _REQUIRED_STEMS - {p.stem for p in required}
    if missing:
        raise SystemExit(f"required compiled specs missing: {sorted(missing)}")
finally:
    db.close()

versions_out.parent.mkdir(parents=True, exist_ok=True)
versions_out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
print(f"wrote {versions_out}")
PY
