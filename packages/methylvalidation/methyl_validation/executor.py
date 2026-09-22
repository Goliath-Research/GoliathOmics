"""
Execute a single pre-planned discovery or predictor-only task (queue worker).
"""

from __future__ import annotations

import csv
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from methyl_utils import load_project

from .config import MonteCarloConfig
from .pipeline_runner import (
    run_pipeline_for_iteration,
    run_pipeline_for_iteration_multiclass,
    run_predictor_only_binary,
    run_predictor_only_multiclass,
)
from .storage_layout import atomic_write_json, run_status_path
from .task_schema import DiscoveryRunTaskV1, parse_discovery_task_file


def _count_csv_data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def _count_run_samples_from_run_dir(run_dir: Path) -> tuple[int, int]:
    train_files = sorted(
        set(list(run_dir.glob("train_*.csv")) + list(run_dir.glob("training_*.csv")))
    )
    canonical_test = sorted(run_dir.glob("test_*.csv"))
    if canonical_test:
        val_files = canonical_test
    else:
        val_files = sorted(set(list(run_dir.glob("val_*.csv")) + list(run_dir.glob("testing_*.csv"))))
    n_train = sum(_count_csv_data_rows(p) for p in train_files)
    n_val = sum(_count_csv_data_rows(p) for p in val_files)
    return int(n_train), int(n_val)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config_for_task(task: DiscoveryRunTaskV1) -> MonteCarloConfig:
    p = Path(task.mc_config_path)
    if not p.is_file():
        raise FileNotFoundError(f"mc_config not found: {p}")
    return MonteCarloConfig.model_validate_json(p.read_text(encoding="utf-8"))


def execute_discovery_task(
    task_path: str,
    *,
    mark_running: bool = True,
    force: bool = False,
) -> int:
    """
    Run a single `DiscoveryRunTaskV1` and write `queue_task_status.json` in the run directory.

    If ``queue_task_status.json`` already has status ``completed`` and ``force`` is false, the
    pipeline is skipped and the command exits 0 (idempotent re-queue / scheduler re-run).

    Returns 0 on success, 1 on failure.
    """
    task = parse_discovery_task_file(task_path)
    run_dir = Path(task.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_status_path(run_dir)
    if not force and status_path.is_file():
        try:
            prev = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
        if str(prev.get("status")) == "completed":
            print(
                f"Skip {task.run_id}: {status_path.name} is already completed "
                f"(use run-task --force to run again).",
                file=sys.stderr,
            )
            return 0

    t0 = _now_iso()
    if mark_running:
        atomic_write_json(
            status_path,
            {
                "schema_version": "1.0",
                "status": "running",
                "task_id": task.task_id,
                "started_at": t0,
                "command": f"run-task {task_path}",
            },
        )

    try:
        config = _load_config_for_task(task)
    except Exception as e:
        atomic_write_json(
            status_path,
            {
                "schema_version": "1.0",
                "status": "failed",
                "task_id": task.task_id,
                "started_at": t0,
                "finished_at": _now_iso(),
                "error": f"load config: {e}",
            },
        )
        print(f"Error: {e}", file=sys.stderr)
        return 1

    n_train, n_val = _count_run_samples_from_run_dir(run_dir)
    if str(task.layout) == "binary":
        rp = load_project(task.project_json)
        comps = rp.get_comparisons()
        if comps:
            s = comps[0]
            pred = Path(task.run_dir) / "predictors" / s.control_group / s.disease_group
        else:
            pred = Path(task.run_dir) / "predictors"
    else:
        pred = Path(task.run_dir) / "predictors"

    step_timings: List[Dict[str, Any]] = []
    ok: bool = False
    err: List[str] = []
    try:
        if bool(task.predictor_only):
            if str(task.layout) == "binary":
                if not task.val_control_csv or not task.val_disease_csv:
                    raise ValueError("Binary predictor-only task needs val_control_csv and val_disease_csv")
                ok, err, step_timings = run_predictor_only_binary(
                    Path(task.project_json),
                    Path(task.val_control_csv),
                    Path(task.val_disease_csv),
                    pred,
                    logs_dir=run_dir / "logs",
                    progress_callback=None,
                )
            else:
                if not task.val_groups_json:
                    raise ValueError("Multiclass predictor-only task needs val_groups_json")
                ok, err, step_timings = run_predictor_only_multiclass(
                    Path(task.project_json),
                    Path(task.val_groups_json),
                    pred,
                    logs_dir=run_dir / "logs",
                    progress_callback=None,
                )
        else:
            # discovery (full iteration pipeline)
            det: Optional[Path] = None
            if task.detector_step_override_path:
                det = Path(str(task.detector_step_override_path))
            if str(task.layout) == "binary":
                c1: Optional[Path] = (
                    Path(str(task.centroid_group1_override)) if task.centroid_group1_override else None
                )
                c2: Optional[Path] = (
                    Path(str(task.centroid_group2_override)) if task.centroid_group2_override else None
                )
                ok, err, step_timings = run_pipeline_for_iteration(
                    Path(task.project_json),
                    per_cancer_group=bool(task.per_cancer_group),
                    logs_dir=run_dir / "logs",
                    progress_callback=None,
                    centroid_step_overrides={"group1": c1, "group2": c2},
                    detector_step_override=det,
                    skip_centroid=bool(task.skip_centroid),
                    config=config,
                )
            else:
                ok, err, step_timings = run_pipeline_for_iteration_multiclass(
                    Path(task.project_json),
                    per_cancer_group=bool(task.per_cancer_group),
                    logs_dir=run_dir / "logs",
                    progress_callback=None,
                    detector_step_override=det,
                    skip_centroid=bool(task.skip_centroid),
                    config=config,
                )
        for t in step_timings:
            t["run_id"] = task.run_id
            t["run_dir"] = str(run_dir)
            t.setdefault("n_train_samples", n_train)
            t.setdefault("n_val_samples", n_val)
        tjson = run_dir / "queue_local_step_timings.json"
        tjson.write_text(json.dumps(step_timings, indent=2), encoding="utf-8")

        if not ok:
            raise RuntimeError("; ".join(err) if err else "step failure")

        atomic_write_json(
            status_path,
            {
                "schema_version": "1.0",
                "status": "completed",
                "task_id": task.task_id,
                "started_at": t0,
                "finished_at": _now_iso(),
            },
        )
        print(f"Completed {task.run_id} ({status_path.name})")
        return 0
    except Exception as e:
        tb = traceback.format_exc()
        atomic_write_json(
            status_path,
            {
                "schema_version": "1.0",
                "status": "failed",
                "task_id": task.task_id,
                "started_at": t0,
                "finished_at": _now_iso(),
                "error": f"{e}",
                "traceback": tb,
            },
        )
        print(f"Error [{task.run_id}]: {e}\n{tb}", file=sys.stderr)
        return 1
