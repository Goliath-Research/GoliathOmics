#!/bin/bash
# Smoke test: SamplePrepPipeline via methyl-study-start (Admin CLI / direct DB).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/smoke_sample_prep.sh [options]

Options:
  --run-root PATH      Smoke fixture root (default: .smoke/sample_prep under repo)
  --poll-seconds N     Instance poll interval (default: 5)
  --timeout SEC        Max wait per instance (default: 600)
  --remediation        Reserved: run remediation-path smoke (second sample)
  -h, --help           Show this help

Requires:
  - Direct DB env (BACKEND_DB + AZURE_SQL_* or POSTGRES_*)
  - Registered worker claiming tasks via worker-only gateway
  - workflow_versions.json from deploy_workflow_definitions.sh
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_ROOT="$REPO_ROOT/.smoke/sample_prep"
SAMPLE_ID="${SMOKE_SAMPLE_ID:-smoke-1}"
POLL=5
TIMEOUT=600
REMEDIATION=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base) echo "Note: --api-base ignored (poll via direct DB)" >&2; shift 2 ;;
    --run-root) RUN_ROOT="${2:-}"; shift 2 ;;
    --poll-seconds) POLL="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    --remediation) REMEDIATION=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
fi

VERSIONS_FILE="${GOLIATH_ENV_DIR:-/work/goliath/env}/workflow_versions.json"
if [[ ! -f "$VERSIONS_FILE" ]]; then
  VERSIONS_FILE="$REPO_ROOT/.smoke/workflow_versions.json"
fi

SMOKE_RUN_ROOT="$RUN_ROOT" bash "$SCRIPT_DIR/bootstrap_sample_prep_smoke_fixtures.sh" --run-root "$RUN_ROOT" --sample-id "$SAMPLE_ID"

"$PYTHON_BIN" - <<'PY' "$RUN_ROOT" "$VERSIONS_FILE" "$POLL" "$TIMEOUT" "$REMEDIATION" "$SAMPLE_ID" "$REPO_ROOT"
import json
import os
import subprocess
import sys
import time
from pathlib import Path

run_root = Path(sys.argv[1])
versions_file = Path(sys.argv[2])
poll = int(sys.argv[3])
timeout = int(sys.argv[4])
remediation = int(sys.argv[5])
sample_id = sys.argv[6]
repo_root = Path(sys.argv[7])
wf_engine = repo_root / "workflow_engine"
sys.path.insert(0, str(wf_engine))

from rest.connection import resolve_connection_config
from rest.db import open_gateway_db

project_path = run_root / "project.json"

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
if not sample_prep_vid:
    raise SystemExit(
        f"workflow_version_id missing in {versions_file}; run deploy_workflow_definitions.sh"
    )

body = {
    "projectPath": str(project_path.resolve()),
    "workflow_version_id": int(sample_prep_vid),
    "samples": [
        {
            "sampleId": sample_id,
            "sampleDir": str((run_root / "samples" / sample_id).resolve()),
            "fastqPrefix": f"{sample_id}/",
        }
    ],
    "fastqStorage": {
        "type": "file",
        "basePath": str(run_root / "fastq"),
    },
    "sampleStorage": {
        "type": "file",
        "basePath": str(run_root / "archive"),
    },
}
if remediation:
    print("note: --remediation not yet implemented; running default pass-path smoke")

proc = subprocess.run(
    [sys.executable, "-m", "admin.study_start", "sample-prep-start", "-"],
    input=json.dumps(body),
    capture_output=True,
    text=True,
    cwd=str(wf_engine),
    env={**os.environ, "PYTHONPATH": str(wf_engine)},
)
if proc.returncode != 0:
    raise SystemExit(f"methyl-study-start failed: {proc.stderr or proc.stdout}")
started = json.loads(proc.stdout)
instance_id = int(started["instance_id"])
print(json.dumps({"planned_samples": started.get("n_samples"), "context_samples": len(started.get("context_json", {}).get("samples", []))}, indent=2))

config = resolve_connection_config()
db = open_gateway_db(config)
try:
    status = poll_instance(db, instance_id)
finally:
    db.close()
if status != "COMPLETED":
    raise SystemExit(f"SamplePrep smoke failed: {status}")

archive_root = run_root / "archive"
archive_prefix = archive_root / sample_id
h5_path = archive_prefix / "h5" / "21-CG.h5"
manifest_path = archive_prefix / "archive_manifest.json"
if not h5_path.is_file():
    raise SystemExit(f"archive smoke check failed: missing {h5_path}")
if not manifest_path.is_file():
    raise SystemExit(f"archive smoke check failed: missing {manifest_path}")

print("smoke_sample_prep: OK")
print(json.dumps({"instance_id": instance_id, "status": status}, indent=2))
PY
