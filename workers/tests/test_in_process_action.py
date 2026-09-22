"""Tests for InProcessAction handler dispatch."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from methyl_worker.action_catalog import find_catalog_entry
from methyl_worker.actions.base import InProcessAction, _call_in_process_handler
from methyl_worker.task_models.runtime_models import TaskRuntimeContext


class _SampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectPath: str


class _SampleOutput(BaseModel):
    status: str = "ok"


def _three_arg_handler(_cap: str, _name: str, input: BaseModel) -> _SampleOutput:
    return _SampleOutput()


def _four_arg_handler(
    _cap: str,
    _name: str,
    input: BaseModel,
    runtime: TaskRuntimeContext,
) -> _SampleOutput:
    if runtime.workflowNodeKey == "boom":
        raise TypeError("internal handler failure")
    return _SampleOutput()


def _keyword_only_runtime_handler(
    _cap: str,
    _name: str,
    input: BaseModel,
    *,
    runtime: TaskRuntimeContext,
) -> _SampleOutput:
    return _SampleOutput(status=runtime.workflowNodeKey or "ok")


def test_call_in_process_handler_passes_required_keyword_only_runtime() -> None:
    runtime = TaskRuntimeContext.from_wire({"workflowNodeKey": "kw-required"})
    out = _call_in_process_handler(
        _keyword_only_runtime_handler,
        "cap",
        "action",
        _SampleInput(projectPath="/p"),
        runtime,
    )
    assert out.status == "kw-required"


def test_call_in_process_handler_passes_keyword_only_runtime() -> None:
    captured: dict[str, TaskRuntimeContext] = {}

    def _capture(
        _cap: str,
        _name: str,
        _input: BaseModel,
        *,
        runtime: TaskRuntimeContext,
    ) -> _SampleOutput:
        captured["runtime"] = runtime
        return _SampleOutput()

    runtime = TaskRuntimeContext.from_wire({"workflowNodeKey": "kw-only"})
    _call_in_process_handler(_capture, "cap", "action", _SampleInput(projectPath="/p"), runtime)
    assert captured["runtime"].workflowNodeKey == "kw-only"


def test_in_process_action_supports_keyword_only_runtime_handler() -> None:
    captured: dict[str, TaskRuntimeContext] = {}

    def _capture(
        _cap: str,
        _name: str,
        _input: BaseModel,
        *,
        runtime: TaskRuntimeContext,
    ) -> _SampleOutput:
        captured["runtime"] = runtime
        return _SampleOutput()

    entry = find_catalog_entry("validation.plan_iterations")
    assert entry is not None
    action = InProcessAction(_capture, entry=entry)
    action.execute(
        {
            "projectPath": "/work/demo/project.json",
            "featureIterations": 1,
            "workflowNodeKey": "plan-kw",
        }
    )
    assert captured["runtime"].workflowNodeKey == "plan-kw"


def test_in_process_action_calls_three_arg_handler_without_runtime() -> None:
    entry = find_catalog_entry("validation.plan_iterations")
    assert entry is not None
    action = InProcessAction(_three_arg_handler, entry=entry)
    result = action.execute({"projectPath": "/work/demo/project.json", "featureIterations": 1})
    assert result.result_code == 0


def test_in_process_action_propagates_internal_type_error_from_four_arg_handler() -> None:
    entry = find_catalog_entry("validation.plan_iterations")
    assert entry is not None
    action = InProcessAction(_four_arg_handler, entry=entry)
    with pytest.raises(TypeError, match="internal handler failure"):
        action.execute(
            {
                "projectPath": "/work/demo/project.json",
                "featureIterations": 1,
                "workflowNodeKey": "boom",
            }
        )


def test_in_process_action_passes_runtime_to_four_arg_handler() -> None:
    captured: dict[str, TaskRuntimeContext] = {}

    def _capture_runtime(
        _cap: str,
        _name: str,
        _input: BaseModel,
        runtime: TaskRuntimeContext,
    ) -> _SampleOutput:
        captured["runtime"] = runtime
        return _SampleOutput()

    entry = find_catalog_entry("validation.plan_iterations")
    assert entry is not None
    action = InProcessAction(_capture_runtime, entry=entry)
    action.execute(
        {
            "projectPath": "/work/demo/project.json",
            "featureIterations": 1,
            "workflowNodeKey": "plan-1",
            "resolvedConfig": {"n_iterations": 2},
        }
    )
    runtime = captured["runtime"]
    assert runtime.workflowNodeKey == "plan-1"
    assert runtime.validationProfile is not None
    assert runtime.validationProfile.n_iterations == 2
