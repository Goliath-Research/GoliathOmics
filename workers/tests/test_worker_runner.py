"""Tests for poll loop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from methyl_worker.action_execution import ActionExecutionResult
from methyl_worker.client import TaskClaim, TaskPollResult, WorkflowRestClient
from methyl_worker.runner import WorkerRunner
from methyl_worker.task_models.sample_prep_models import MarkFailedTaskOutput


def test_run_once_no_task() -> None:
    client = MagicMock(spec=WorkflowRestClient)
    client.request_task.return_value = TaskPollResult(claim=None)
    runner = WorkerRunner(client, 1, "tok", poll_seconds=0.01)
    assert runner.run_once() is False
    client.request_task.assert_called_once()


def test_run_once_processes_task() -> None:
    client = MagicMock(spec=WorkflowRestClient)
    claim = TaskClaim(
        node_execution_id=99,
        workflow_instance_id=1,
        action_name="sample.qc_failed",
        capability="sample.mark-failed",
        input_json={"sampleId": "S1", "sampleDir": "/work/samples/S1"},
    )
    client.request_task.return_value = TaskPollResult(claim=claim)
    client.heartbeat.return_value = MagicMock(
        control=MagicMock(desired_state="ACTIVE", command="NONE")
    )
    runner = WorkerRunner(client, 1, "tok", poll_seconds=0.01, heartbeat_seconds=3600)

    with patch(
        "methyl_worker.runner.execute_task",
        return_value=ActionExecutionResult(
            result_code=0,
            output=MarkFailedTaskOutput(status="QC_FAILED", sampleId="S1"),
        ),
    ):
        assert runner.run_once() is True

    client.submit_result.assert_called_once()
    args = client.submit_result.call_args[0]
    assert args[0] == 99
    assert args[3] == 0


def test_run_once_fails_task_on_input_validation_error() -> None:
    client = MagicMock(spec=WorkflowRestClient)
    claim = TaskClaim(
        node_execution_id=42,
        workflow_instance_id=1,
        action_name="pipeline.centroid",
        capability="methyl-centroid",
        input_json={"project": "demo"},
    )
    client.request_task.return_value = TaskPollResult(claim=claim)
    client.heartbeat.return_value = MagicMock(
        control=MagicMock(desired_state="ACTIVE", command="NONE")
    )
    runner = WorkerRunner(client, 1, "tok", poll_seconds=0.01, heartbeat_seconds=3600)

    assert runner.run_once() is True

    client.fail_task.assert_called_once()
    args, kwargs = client.fail_task.call_args
    assert args[0] == 42
    assert args[3] == 4001
    client.submit_result.assert_not_called()
