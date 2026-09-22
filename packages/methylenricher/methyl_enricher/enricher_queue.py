"""
Distributed queue for per-comparison enricher tasks (post-freeze production).
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .enricher_completeness import (
    TASK_STATUS_FILENAME,
    read_task_status,
    resolve_expected_libraries,
)
from methyl_utils.action_config_resolver import resolve_for_project
from .ensure_complete import run_project_ensure_complete, verify_project_complete
from .project_resolver import resolve_enricher_paths_per_cancer_group


class EnricherComparisonTaskV1(BaseModel):
    """One enricher worker task: all libraries for a single comparison."""

    model_config = ConfigDict(extra="allow")

    task_schema_version: str = "1.0"
    task_id: str
    mode: str = "enricher_comparison"
    project_json: str
    comparison_label: str
    input_file: str
    output_dir: str
    libraries: List[str]
    modules: bool = True
    enricher_step_config: Dict[str, Any] = Field(default_factory=dict)


def _queue_root(project_json: Path) -> Path:
    root = production_enricher_queue_root(project_json)
    root.mkdir(parents=True, exist_ok=True)
    return root


def production_enricher_queue_root(project_json: Path) -> Path:
    from .enricher_completeness import production_enricher_root

    return production_enricher_root(project_json) / "queue"


def plan_enricher_tasks(
    project_path: Path,
    *,
    step_override_path: Optional[Path] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Write queue/tasks/*.json and plan.json under production/enricher/queue/."""
    from methyl_utils import load_project
    from .config import EnricherStepConfig

    project_path = Path(project_path)
    project = load_project(project_path)
    step_cfg = dict(resolve_for_project("enricher", project))
    if step_override_path and step_override_path.exists():
        step_cfg = {**step_cfg, **json.loads(step_override_path.read_text())}
    enricher_config = EnricherStepConfig.model_validate(step_cfg)
    libraries = resolve_expected_libraries(
        libraries=enricher_config.libraries,
        library_preset=enricher_config.library_preset,
    )
    per_group = resolve_enricher_paths_per_cancer_group(project_path, step_override_path)
    qroot = _queue_root(project_path)
    tasks_dir = qroot / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    runs: List[Dict[str, Any]] = []
    for paths, label in per_group:
        safe = label.replace("/", "_").replace(" ", "_")
        task_id = f"enricher_{safe}"
        task_path = tasks_dir / f"{task_id}.json"
        if task_path.exists() and not overwrite:
            existing = json.loads(task_path.read_text(encoding="utf-8"))
            runs.append(
                {
                    "task_id": task_id,
                    "comparison_label": label,
                    "task_json": str(task_path),
                    "input_file": existing.get("input_file"),
                    "output_dir": existing.get("output_dir"),
                }
            )
            continue

        task = EnricherComparisonTaskV1(
            task_id=task_id,
            project_json=str(project_path.resolve()),
            comparison_label=label,
            input_file=str(Path(paths.input_file).resolve()),
            output_dir=str(Path(paths.output_dir).resolve()),
            libraries=libraries,
            modules=bool(enricher_config.modules),
            enricher_step_config=enricher_config.model_dump(mode="python", exclude_none=True),
        )
        task_path.write_text(task.model_dump_json(indent=2), encoding="utf-8")
        runs.append(
            {
                "task_id": task_id,
                "comparison_label": label,
                "task_json": str(task_path),
                "input_file": task.input_file,
                "output_dir": task.output_dir,
            }
        )

    plan = {
        "project_json": str(project_path.resolve()),
        "n_tasks": len(runs),
        "libraries": libraries,
        "runs": runs,
    }
    (qroot / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def export_enricher_queue(project_path: Path) -> Dict[str, Any]:
    """Write queue_manifest.jsonl and commands.sh."""
    project_path = Path(project_path)
    qroot = _queue_root(project_path)
    plan_path = qroot / "plan.json"
    if not plan_path.is_file():
        print(f"Error: missing {plan_path} (run plan-tasks first).", file=sys.stderr)
        sys.exit(1)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    runs = plan.get("runs") or []
    mpath = qroot / "queue_manifest.jsonl"
    lines: List[str] = []
    shell = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    for r in runs:
        tjs = r.get("task_json")
        if not tjs:
            continue
        cmd = ["methyl-enricher", "run-task", "--task", tjs]
        record = {
            "task_id": r.get("task_id"),
            "stage": "enricher_comparison",
            "comparison_label": r.get("comparison_label"),
            "command": cmd,
            "expected_outputs": [
                str(Path(r["output_dir"]) / "enrichment_merged.csv"),
                str(Path(r["output_dir"]) / TASK_STATUS_FILENAME),
            ],
        }
        lines.append(json.dumps(record, ensure_ascii=True))
        shell.append(
            " ".join(
                ["methyl-enricher", "run-task", "--task", shlex.quote(str(tjs))]
            )
        )
    mpath.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    csh = qroot / "commands.sh"
    csh.write_text("\n".join(shell) + "\n", encoding="utf-8")
    csh.chmod(0o755)
    summary = {
        "project_json": str(project_path),
        "n_tasks": len(runs),
        "manifest": str(mpath),
        "commands_sh": str(csh),
    }
    (qroot / "queue_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_enricher_task_file(path: Path) -> EnricherComparisonTaskV1:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return EnricherComparisonTaskV1.model_validate(raw)


def execute_enricher_task(
    task_path: Path,
    *,
    force: bool = False,
) -> int:
    """Run one comparison ensure-complete; return 0 on complete else 1."""
    task = parse_enricher_task_file(task_path)
    out_dir = Path(task.output_dir)
    status_path = out_dir / TASK_STATUS_FILENAME
    if not force and status_path.is_file():
        st = read_task_status(out_dir)
        if st and st.get("status") == "completed":
            print(f"[INFO] Task {task.task_id} already completed; skip (use --force to redo)")
            return 0

    all_ok, reports = run_project_ensure_complete(
        Path(task.project_json),
        comparison=task.comparison_label,
        force=force,
        verify_only=False,
    )
    report = reports.get(task.comparison_label)
    if report and report.complete:
        return 0
    return 1
