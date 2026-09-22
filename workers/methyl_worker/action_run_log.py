"""Append-only JSONL audit log for workflow ACTION execution (all categories)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def action_run_log_path(log_root: Path) -> Path:
    """Canonical unified workflow timeline at the resolved log root."""
    return Path(log_root) / "action_run_log.jsonl"


def monte_carlo_runs_root_from_path(path: Path) -> Optional[Path]:
    """Walk parents until a ``monte_carlo_runs`` directory is found."""
    resolved = path.expanduser().resolve()
    for ancestor in (resolved, *resolved.parents):
        if ancestor.name == "monte_carlo_runs":
            return ancestor
    return None


def resolve_workflow_action_log_root(
    entry: Any,
    input_json: Mapping[str, Any],
) -> Optional[Path]:
    """
    Resolve where to append action_run_log.jsonl for this action.

    Prefers explicit ``monteCarloRunsRoot``, then any path under ``monte_carlo_runs``,
    then validation project output, then generic project output / sampleDir.
    """
    explicit = input_json.get("monteCarloRunsRoot")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()

    for key in ("runDir", "outputDir", "targetRunDir"):
        val = input_json.get(key)
        if val:
            mc_root = monte_carlo_runs_root_from_path(Path(str(val)))
            if mc_root is not None:
                return mc_root

    from .action_skip import _resolve_monte_carlo_runs_root, resolve_action_output_dir

    out_dir = resolve_action_output_dir(entry, input_json)
    if out_dir is not None:
        mc_root = monte_carlo_runs_root_from_path(out_dir)
        if mc_root is not None:
            return mc_root

    if getattr(entry, "category", None) == "validation":
        try:
            return _resolve_monte_carlo_runs_root(input_json)
        except Exception:
            pass

    project = input_json.get("projectPath") or input_json.get("project")
    if project:
        try:
            from methyl_utils import load_project

            cfg = load_project(str(project))
            return Path(cfg.output_base) / cfg.project_name
        except Exception:
            pass

    sample_dir = input_json.get("sampleDir")
    if sample_dir:
        return Path(str(sample_dir)).expanduser().resolve()

    return None


def append_action_run_log(
    log_root: Path,
    *,
    action: str,
    capability: str,
    result_code: int = 0,
    category: Optional[str] = None,
    run_dir: Optional[str] = None,
    run_key: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
    workflow_node_key: Optional[str] = None,
    started_at_utc: Optional[str] = None,
    finished_at_utc: Optional[str] = None,
    duration_ms: Optional[int] = None,
    status: Optional[str] = None,
    exit_code: Optional[int] = None,
    skipped: bool = False,
    skip_reason: Optional[str] = None,
    action_revision: Optional[str] = None,
    input_signature: Optional[str] = None,
    output_signature: Optional[str] = None,
) -> Path:
    """Append one JSON line to {log_root}/action_run_log.jsonl."""
    log_root = Path(log_root)
    log_path = action_run_log_path(log_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    finished = finished_at_utc or _utc_now_iso()
    record = {
        "ts_utc": finished,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc or finished,
        "duration_ms": duration_ms,
        "status": status,
        "exit_code": exit_code,
        "category": category,
        "action": action,
        "capability": capability,
        "result_code": result_code,
        "run_dir": run_dir,
        "run_key": run_key,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "workflow_node_key": workflow_node_key,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "action_revision": action_revision,
        "input_signature": input_signature,
        "output_signature": output_signature,
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return log_path


def task_inputs_for_log(input_json: Mapping[str, Any]) -> Dict[str, Any]:
    """Task payload for drill-down (runtime control fields stripped)."""
    from .task_validation import strip_runtime_input

    return strip_runtime_input(dict(input_json))
