"""Tests for unified workflow action_run_log.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from methyl_worker.action_run_log import (
    action_run_log_path,
    append_action_run_log,
    monte_carlo_runs_root_from_path,
    resolve_workflow_action_log_root,
    task_inputs_for_log,
)
from methyl_worker.handlers import execute_task
from methyl_worker.task_models.pipeline_models import ProgressionTaskOutput
from methyl_worker.task_models.validation_models import ValidationPlanTaskOutput


def test_append_action_run_log_writes_jsonl(tmp_path: Path) -> None:
    append_action_run_log(
        tmp_path,
        action="validation.stability",
        capability="validation.stability",
        category="validation",
        result_code=0,
        started_at_utc="2026-06-29T10:00:00Z",
        finished_at_utc="2026-06-29T10:01:30Z",
        duration_ms=90000,
        status="ok",
        exit_code=0,
        inputs={"projectPath": "/work/p/project.json"},
        outputs={"status": "ok", "duration_ms": 90000},
    )
    log_path = action_run_log_path(tmp_path)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "validation.stability"
    assert record["category"] == "validation"
    assert record["result_code"] == 0
    assert record["duration_ms"] == 90000
    assert record["started_at_utc"] == "2026-06-29T10:00:00Z"
    assert record["finished_at_utc"] == "2026-06-29T10:01:30Z"
    assert record["status"] == "ok"
    assert record["exit_code"] == 0


def test_monte_carlo_runs_root_from_path(tmp_path: Path) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    run_dir = mc_root / "run_001" / "chr1"
    run_dir.mkdir(parents=True)
    assert monte_carlo_runs_root_from_path(run_dir) == mc_root.resolve()
    assert monte_carlo_runs_root_from_path(tmp_path / "nowhere") is None


def test_resolve_workflow_action_log_root_from_run_dir(tmp_path: Path) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    run_dir = mc_root / "run_001"
    run_dir.mkdir(parents=True)

    entry = type("Entry", (), {"category": "pipeline"})()
    resolved = resolve_workflow_action_log_root(
        entry,
        {"runDir": str(run_dir), "outputDir": str(run_dir / "out")},
    )
    assert resolved == mc_root.resolve()


def test_task_inputs_for_log_strips_runtime_fields() -> None:
    payload = {
        "projectPath": "/work/p/project.json",
        "runDir": "/work/r",
        "forceRerun": True,
        "workflowNodeKey": "node-1",
        "resolvedConfig": {"validation": {}},
    }
    logged = task_inputs_for_log(payload)
    assert logged == {"projectPath": "/work/p/project.json", "runDir": "/work/r"}
    assert "forceRerun" not in logged
    assert "workflowNodeKey" not in logged


def test_execute_task_appends_validation_action_log(tmp_path: Path, monkeypatch) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    mc_root.mkdir()
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")

    started = datetime(2026, 6, 29, 10, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 6, 29, 10, 0, 5, tzinfo=timezone.utc)
    fake_output = ValidationPlanTaskOutput(
        status="ok",
        n_iterations=0,
        started_at_utc=started,
        finished_at_utc=finished,
        duration_ms=5000,
    )

    class FakeAction:
        def execute(self, _input_json, *, handle=None):
            from methyl_worker.action_execution import ActionExecutionResult

            return ActionExecutionResult(result_code=0, output=fake_output)

    monkeypatch.setattr(
        "methyl_worker.handlers.build_action_from_catalog",
        lambda _entry, _mod: FakeAction(),
    )
    monkeypatch.setattr(
        "methyl_worker.action_run_log.resolve_workflow_action_log_root",
        lambda _entry, _input: mc_root,
    )
    execute_task(
        "validation.plan-iterations",
        "validation.plan_iterations",
        {"projectPath": str(project)},
    )

    log_path = action_run_log_path(mc_root)
    assert log_path.is_file()
    record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["action"] == "validation.plan_iterations"
    assert record["category"] == "validation"
    assert record["duration_ms"] == 5000
    assert record["outputs"]["n_iterations"] == 0


def test_execute_task_appends_pipeline_action_log(tmp_path: Path, monkeypatch) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    run_dir = mc_root / "run_001"
    run_dir.mkdir(parents=True)
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")

    started = datetime(2026, 6, 29, 11, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 6, 29, 11, 2, 13, tzinfo=timezone.utc)
    fake_output = ProgressionTaskOutput(
        status="ok",
        n_comparisons=2,
        started_at_utc=started,
        finished_at_utc=finished,
        duration_ms=133000,
    )

    class FakeAction:
        def execute(self, _input_json, *, handle=None):
            from methyl_worker.action_execution import ActionExecutionResult

            return ActionExecutionResult(result_code=0, output=fake_output)

    monkeypatch.setattr(
        "methyl_worker.handlers.build_action_from_catalog",
        lambda _entry, _mod: FakeAction(),
    )
    with patch("methyl_worker.action_skip.record_action_execution"):
        execute_task(
            "pipeline.progression",
            "pipeline.progression",
            {
                "tool": "MethylProgression",
                "projectPath": str(project),
                "outputDir": str(run_dir),
            },
        )

    log_path = action_run_log_path(mc_root)
    assert log_path.is_file()
    record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["action"] == "pipeline.progression"
    assert record["category"] == "modeling"
    assert record["duration_ms"] == 133000
    assert record["run_dir"] == str(run_dir)
    assert record["inputs"]["projectPath"] == str(project)
    assert record["outputs"]["n_comparisons"] == 2
