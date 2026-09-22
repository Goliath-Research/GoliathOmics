"""
Build ``queue_manifest.jsonl`` and ``commands.sh`` for a central work queue.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List


from .storage_layout import (
    commands_sh_path,
    queue_manifest_path,
    queue_summary_path,
)


def _expected_outputs_for_run(r: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    pj = r.get("project_json")
    rd = r.get("run_dir")
    if pj:
        out.append(str(pj))
    if rd:
        out.append(str(Path(str(rd)) / "queue_task_status.json"))
    return out


def export_queue_artifacts(
    monte_carlo_runs_root: Path,
    *,
    stage: str = "discovery",
) -> Dict[str, Any]:
    """
    Read ``queue/plan_runs.json`` and write ``queue_manifest.jsonl`` + ``commands.sh`` + ``queue_summary.json``.
    """
    plan = monte_carlo_runs_root / "queue" / "plan_runs.json"
    if not plan.is_file():
        print(f"Error: missing {plan} (run plan-runs first).", file=sys.stderr)
        sys.exit(1)
    with open(plan, encoding="utf-8") as f:
        pld = json.load(f)
    runs: List[Dict[str, Any]] = pld.get("runs") or []
    mpath = queue_manifest_path(monte_carlo_runs_root)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    shell: List[str] = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    for r in runs:
        tjs = r.get("task_json")
        if not tjs:
            continue
        tjs = str(tjs)
        cmd = ["methyl-validation", "run-task", "--task", tjs]
        record: Dict[str, Any] = {
            "task_id": f"discovery_{r.get('run_id', '')}",
            "stage": stage,
            "run_id": r.get("run_id"),
            "command": cmd,
            "env": {"shared_monte_carlo_runs_root": str(monte_carlo_runs_root)},
            "expected_outputs": _expected_outputs_for_run(r),
        }
        lines.append(json.dumps(record, ensure_ascii=True))
        shell.append(
            " ".join(
                [
                    "methyl-validation",
                    "run-task",
                    "--task",
                    shlex.quote(tjs),
                ]
            )
        )
    mpath.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    csh = commands_sh_path(monte_carlo_runs_root)
    csh.write_text("\n".join(shell) + "\n", encoding="utf-8")
    csh.chmod(0o755)
    summ = {
        "queue_schema_version": "1.0",
        "n_tasks": len(lines),
        "stage": stage,
        "monte_carlo_runs_root": str(monte_carlo_runs_root),
        "manifest": str(mpath),
        "commands_sh": str(csh),
    }
    queue_summary_path(monte_carlo_runs_root).write_text(
        json.dumps(summ, indent=2) + "\n", encoding="utf-8"
    )
    return summ
