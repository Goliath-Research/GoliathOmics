"""Tests for worker-local in-process handler DI."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from methyl_worker.depends import (
    Depends,
    call_in_process_handler,
    get_logger,
    get_monte_carlo_runs_root,
    get_project_path,
    get_runtime,
)
from methyl_worker.task_models.runtime_models import TaskRuntimeContext


class _TinyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectPath: str
    monteCarloRunsRoot: str | None = None


def test_injects_runtime_and_logger() -> None:
    seen: dict = {}

    def handler(
        capability: str,
        action_name: str,
        input: BaseModel,
        runtime: TaskRuntimeContext = Depends(get_runtime),
        log: logging.Logger = Depends(get_logger),
    ) -> BaseModel:
        seen["capability"] = capability
        seen["action_name"] = action_name
        seen["runtime"] = runtime
        seen["log"] = log
        return input

    tiny = _TinyInput(projectPath="/tmp/p.json", monteCarloRunsRoot="/tmp/mc")
    out = call_in_process_handler(
        handler,
        "cap",
        "demo.action",
        tiny,
        TaskRuntimeContext(forceRerun=True),
    )
    assert isinstance(out, _TinyInput)
    assert seen["capability"] == "cap"
    assert seen["action_name"] == "demo.action"
    assert seen["runtime"].forceRerun is True
    assert isinstance(seen["log"], logging.Logger)
    assert "demo.action" in seen["log"].name


def test_legacy_runtime_kwarg_without_depends() -> None:
    seen: dict = {}

    def handler(capability: str, action_name: str, input: BaseModel, runtime=None) -> BaseModel:
        seen["runtime"] = runtime
        return input

    tiny = _TinyInput(projectPath="/tmp/p.json", monteCarloRunsRoot="/tmp/mc")
    call_in_process_handler(handler, "cap", "demo.legacy", tiny, {"forceRerun": True})
    assert isinstance(seen["runtime"], TaskRuntimeContext)
    assert seen["runtime"].forceRerun is True


def test_handlers_without_extra_params_still_work() -> None:
    def handler(capability: str, action_name: str, input: BaseModel) -> BaseModel:
        return input

    tiny = _TinyInput(projectPath="/tmp/p.json", monteCarloRunsRoot="/tmp/mc")
    out = call_in_process_handler(handler, "cap", "demo.plain", tiny, None)
    assert out.projectPath == "/tmp/p.json"


def test_path_helpers(tmp_path: Path) -> None:
    mc = tmp_path / "monte_carlo_runs"
    mc.mkdir()
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")

    seen: dict = {}

    def handler(
        capability: str,
        action_name: str,
        input: BaseModel,
        project_path: Path = Depends(get_project_path),
        mc_root: Path = Depends(get_monte_carlo_runs_root),
    ) -> BaseModel:
        seen["project_path"] = project_path
        seen["mc_root"] = mc_root
        return input

    tiny = _TinyInput(projectPath=str(project), monteCarloRunsRoot=str(mc))
    call_in_process_handler(handler, "cap", "demo.paths", tiny, None)
    assert seen["project_path"] == project
    assert seen["mc_root"] == mc.resolve()


class _OtherInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


def test_coerces_third_arg_to_concrete_annotation() -> None:
    seen: dict = {}

    def handler(capability: str, action_name: str, input: _TinyInput) -> BaseModel:
        seen["type"] = type(input)
        return input

    class _Loose(BaseModel):
        projectPath: str
        monteCarloRunsRoot: str | None = None

    loose = _Loose(projectPath="/tmp/p.json", monteCarloRunsRoot="/tmp/mc")
    out = call_in_process_handler(handler, "cap", "demo.coerce", loose, None)
    assert isinstance(out, _TinyInput)
    assert seen["type"] is _TinyInput


def test_mismatched_handler_annotation_raises_validation_error() -> None:
    def handler(capability: str, action_name: str, input: _OtherInput) -> BaseModel:
        return input

    tiny = _TinyInput(projectPath="/tmp/p.json")
    with pytest.raises(ValidationError):
        call_in_process_handler(handler, "cap", "demo.mismatch", tiny, None)
