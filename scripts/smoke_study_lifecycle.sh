#!/bin/bash
# Smoke test: stubbed SamplePrep + StudyValidationLifecycle via direct DB.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/smoke_study_lifecycle.sh [options]

Options:
  --project PATH       Project JSON (default: smoke project in pca1_5_cg bundle)
  --poll-seconds N     Instance poll interval (default: 5)
  --timeout SEC        Max wait per instance (default: 600)
  -h, --help           Show this help

Requires:
  - Direct DB env (BACKEND_DB + AZURE_SQL_* or POSTGRES_*)
  - Registered worker claiming tasks via worker-only gateway
  - workflow_versions.json from deploy_workflow_definitions.sh
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROJECT_PATH="$REPO_ROOT/workflow_engine/domain/checks/pca1_5_cg/configs/project_Healthy_vs_PCa1-5-CG_smoke.json"
VERSIONS_FILE="${GOLIATH_ENV_DIR:-/work/goliath/env}/workflow_versions.json"
POLL=5
TIMEOUT=600

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base) echo "Note: --api-base ignored (direct DB)" >&2; shift 2 ;;
    --project) PROJECT_PATH="${2:-}"; shift 2 ;;
    --poll-seconds) POLL="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
fi

[[ -f "$PROJECT_PATH" ]] || { echo "Project not found: $PROJECT_PATH" >&2; exit 1; }

"$PYTHON_BIN" - <<'PY' "$PROJECT_PATH" "$VERSIONS_FILE" "$POLL" "$TIMEOUT" "$REPO_ROOT"
import json
import os
import subprocess
import sys
import time
from pathlib import Path

project_path = sys.argv[1]
versions_file = Path(sys.argv[2])
poll = int(sys.argv[3])
timeout = int(sys.argv[4])
repo_root = Path(sys.argv[5])
wf_engine = repo_root / "workflow_engine"
sys.path.insert(0, str(wf_engine))

from rest.connection import resolve_connection_config
from rest.db import open_gateway_db

def poll_instance(db, instance_id: int) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        inst = db.get_workflow_instance(instance_id)
        status = inst.get("status") or inst.get("workflow_status")
        print(f"instance {instance_id} status={status}", flush=True)
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            return str(status)
        time.sleep(poll)
    raise TimeoutError(f"instance {instance_id} not terminal after {timeout}s")

versions = {}
if versions_file.is_file():
    versions = json.loads(versions_file.read_text(encoding="utf-8"))

sample_prep_vid = (
    versions.get("SamplePrepPipeline", {}).get("workflow_version_id")
    or versions.get("sample_prep_compiled", {}).get("workflow_version_id")
)
lifecycle_vid = (
    versions.get("StudyValidationLifecycle", {}).get("workflow_version_id")
    or versions.get("study_validation_lifecycle_compiled", {}).get("workflow_version_id")
)

if not sample_prep_vid or not lifecycle_vid:
    raise SystemExit(
        f"workflow_version_id missing in {versions_file}; run deploy_workflow_definitions.sh"
    )

env = {**os.environ, "PYTHONPATH": str(wf_engine)}

prep_body = {
    "projectPath": project_path,
    "workflow_version_id": int(sample_prep_vid),
    "primaryAnalyte": "buffy_coat",
    "isCfdna": False,
    "referenceFasta": "/work/goliath/data/reference.fa",
    "fastqStorage": {
        "type": "file",
        "basePath": "/work/goliath/runs/smoke/fastq",
    },
    "samples": [
        {
            "sampleId": "smoke-1",
            "sampleDir": "/work/goliath/runs/smoke/samples/smoke-1",
            "fastqPrefix": "smoke-1/",
        }
    ],
}
proc = subprocess.run(
    [sys.executable, "-m", "admin.study_start", "sample-prep-start", "-"],
    input=json.dumps(prep_body),
    capture_output=True,
    text=True,
    cwd=str(wf_engine),
    env=env,
)
if proc.returncode != 0:
    raise SystemExit(f"sample-prep-start failed: {proc.stderr or proc.stdout}")
prep = json.loads(proc.stdout)
prep_id = int(prep["instance_id"])

config = resolve_connection_config()
db = open_gateway_db(config)
try:
    prep_status = poll_instance(db, prep_id)
finally:
    db.close()
if prep_status != "COMPLETED":
    raise SystemExit(f"SamplePrep smoke failed: {prep_status}")

val_body = {
    "projectPath": project_path,
    "workflow_version_id": int(lifecycle_vid),
    "featureIterations": 2,
    "seed": 42,
}
proc = subprocess.run(
    [sys.executable, "-m", "admin.study_start", "validation-start", "-"],
    input=json.dumps(val_body),
    capture_output=True,
    text=True,
    cwd=str(wf_engine),
    env=env,
)
if proc.returncode != 0:
    raise SystemExit(f"validation-start failed: {proc.stderr or proc.stdout}")
val = json.loads(proc.stdout)
val_id = int(val["instance_id"])

db = open_gateway_db(config)
try:
    val_status = poll_instance(db, val_id)
finally:
    db.close()
if val_status != "COMPLETED":
    raise SystemExit(f"StudyValidationLifecycle smoke failed: {val_status}")

print("smoke_study_lifecycle: OK")
print(json.dumps({"sample_prep_instance_id": prep_id, "validation_instance_id": val_id}, indent=2))
PY
