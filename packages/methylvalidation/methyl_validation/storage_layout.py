"""
Shared /work (or any output_base) layout for queue metadata and claims.

Conventions (under each project's ``.../monte_carlo_runs/``)::

  queue/
    mc_config.json              # Pydantic snapshot of MonteCarloConfig
    mc_config.effective.json    # Informational activation-aware operator view
    plan_runs.json              # Machine-readable list of planned runs
    tasks/                      # Per-run task JSON for ``run-task --task ...``
    queue_manifest.jsonl        # (export-queue) one JSON object per line
    queue_summary.json          # (export-queue) counts
    claims/                     # Optional file-based worker leases
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


def queue_subdir(monte_carlo_runs_root: Path) -> Path:
    return monte_carlo_runs_root / "queue"


def tasks_subdir(monte_carlo_runs_root: Path) -> Path:
    return queue_subdir(monte_carlo_runs_root) / "tasks"


def claims_subdir(monte_carlo_runs_root: Path) -> Path:
    return queue_subdir(monte_carlo_runs_root) / "claims"


def mc_config_snapshot_path(monte_carlo_runs_root: Path) -> Path:
    return queue_subdir(monte_carlo_runs_root) / "mc_config.json"


def plan_runs_path(monte_carlo_runs_root: Path) -> Path:
    return queue_subdir(monte_carlo_runs_root) / "plan_runs.json"


def queue_manifest_path(monte_carlo_runs_root: Path) -> Path:
    return queue_subdir(monte_carlo_runs_root) / "queue_manifest.jsonl"


def queue_summary_path(monte_carlo_runs_root: Path) -> Path:
    return queue_subdir(monte_carlo_runs_root) / "queue_summary.json"


def commands_sh_path(monte_carlo_runs_root: Path) -> Path:
    return queue_subdir(monte_carlo_runs_root) / "commands.sh"


def run_task_path(monte_carlo_runs_root: Path, run_id: str) -> Path:
    return tasks_subdir(monte_carlo_runs_root) / f"{run_id}.json"


def run_status_path(run_dir: Path) -> Path:
    """Per-run completion record (written by run-task, read by aggregate-results)."""
    return run_dir / "queue_task_status.json"


def atomic_write_json(path: Path, payload: Dict[str, Any], indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
