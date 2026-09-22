"""Append-only JSONL audit log for SamplePrep worker actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from methyl_worker.work_share import append_work_text
except ImportError:  # stale sister: work_share loaded before append_work_text existed
    from methyl_worker.work_share_compat import stale_append_work_text as append_work_text


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sample_prep_log_path(sample_dir: Path, sample_id: str) -> Path:
    return sample_dir / f"{sample_id}.sample_prep_log.jsonl"


def append_sample_prep_log(
    sample_dir: Path,
    *,
    sample_id: str,
    action: str,
    capability: str,
    attempt: int = 1,
    reason: str = "",
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
    result_code: int = 0,
    workflow_node_key: Optional[str] = None,
) -> Path:
    """Append one JSON line to {sampleDir}/{sampleId}.sample_prep_log.jsonl."""
    sample_dir = Path(sample_dir)
    log_path = sample_prep_log_path(sample_dir, sample_id)
    record = {
        "ts_utc": _utc_now_iso(),
        "action": action,
        "capability": capability,
        "attempt": attempt,
        "reason": reason,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "result_code": result_code,
        "workflow_node_key": workflow_node_key,
    }
    payload = json.dumps(record, separators=(",", ":")) + "\n"
    return append_work_text(log_path, payload)
