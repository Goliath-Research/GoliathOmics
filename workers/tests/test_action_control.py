"""Action catalog control flags + WorkerRunner drain/stop behavior."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from methyl_worker.action_catalog import (
    CONTROL_DRAIN_ONLY,
    CONTROL_STOPPABLE,
    control_for,
    find_catalog_entry,
)
from methyl_worker.action_execution import ActionExecutionResult
from methyl_worker.client import HeartbeatAck, TaskClaim, TaskPollResult, WorkerControlAck
from methyl_worker.execution_handle import (
    WORKER_STOPPED_ERROR_CODE,
    ExecutionHandle,
    WorkerStoppedError,
    run_cancellable,
)
from methyl_worker.runner import WorkerRunner
from methyl_worker.task_models.sample_prep_models import DeleteTaskOutput


def test_catalog_control_defaults_and_align() -> None:
    align = find_catalog_entry("sample.methylgrapher_wgbs_align")
    assert align is not None
    assert align.control == CONTROL_STOPPABLE
    assert align.control.can_stop is True
    assert align.control.can_pause is False

    delete = find_catalog_entry("sample.delete_bam")
    assert delete is not None
    assert delete.control == CONTROL_DRAIN_ONLY
    assert delete.control.can_stop is False

    exported = align.to_catalog_dict()["control"]
    assert exported == {"can_pause": False, "can_continue": False, "can_stop": True}

    assert control_for("unknown.action").can_stop is True


def test_run_cancellable_stop() -> None:
    handle = ExecutionHandle()

    def cancel_soon() -> None:
        time.sleep(0.3)
        handle.request_cancel()

    threading.Thread(target=cancel_soon, daemon=True).start()
    try:
        run_cancellable(["sleep", "30"], handle=handle, poll_seconds=0.1)
        raise AssertionError("expected WorkerStoppedError")
    except WorkerStoppedError:
        pass


def test_run_cancellable_large_stdout_no_deadlock() -> None:
    """Pipe buffers are ~64 KiB; draining via communicate must not hang."""
    import sys

    # Build the blob in the child so argv stays under Linux MAX_ARG_STRLEN
    # (32 pages: 128 KiB on 4 KiB-page x86 CI agents).
    completed = run_cancellable(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200_000)"],
        poll_seconds=0.1,
    )
    assert completed.returncode == 0
    assert len(completed.stdout) >= 200_000


def test_runner_drain_skips_claim() -> None:
    client = MagicMock()
    client.request_task.return_value = TaskPollResult(
        claim=None,
        control=WorkerControlAck(desired_state="DRAINING", command="DRAIN"),
    )
    runner = WorkerRunner(client, 1, "tok", poll_seconds=0.01)
    assert runner.run_once() is False
    client.request_task.assert_called_once()


def test_runner_stop_cancels_when_can_stop() -> None:
    claim = TaskClaim(
        node_execution_id=99,
        workflow_instance_id=1,
        action_name="sample.methylgrapher_wgbs_align",
        capability="methylgrapher.wgbs_align",
        input_json={"sampleId": "S1", "sampleDir": "/tmp/S1"},
    )
    client = MagicMock()
    client.request_task.return_value = TaskPollResult(
        claim=claim,
        control=WorkerControlAck(desired_state="ACTIVE", command="NONE"),
    )
    client.heartbeat.return_value = HeartbeatAck(
        1, WorkerControlAck("STOPPING", "STOP")
    )

    handles: List[ExecutionHandle] = []

    def fake_execute(
        capability: str,
        action_name: str,
        input_json: Dict[str, Any],
        *,
        handle: Optional[ExecutionHandle] = None,
    ):
        assert handle is not None
        handles.append(handle)
        deadline = time.time() + 5
        while time.time() < deadline:
            if handle.is_cancelled:
                raise WorkerStoppedError("stopped")
            time.sleep(0.05)
        raise AssertionError("cancel never arrived")

    runner = WorkerRunner(client, 1, "tok", heartbeat_seconds=0.05)
    with (
        patch("methyl_worker.runner.execute_task", side_effect=fake_execute),
        patch("methyl_worker.runner.validate_task_input"),
        patch("methyl_worker.runner.validate_task_output"),
    ):
        assert runner.run_once() is True

    assert handles and handles[0].is_cancelled
    client.fail_task.assert_called_once()
    assert client.fail_task.call_args[0][3] == WORKER_STOPPED_ERROR_CODE


def test_runner_stop_refuses_when_not_can_stop() -> None:
    claim = TaskClaim(
        node_execution_id=100,
        workflow_instance_id=1,
        action_name="sample.delete_bam",
        capability="sample.delete-bam",
        input_json={"sampleId": "S1", "sampleDir": "/tmp/S1"},
    )
    client = MagicMock()
    client.request_task.return_value = TaskPollResult(
        claim=claim,
        control=WorkerControlAck("ACTIVE", "NONE"),
    )
    client.heartbeat.return_value = HeartbeatAck(
        1, WorkerControlAck("STOPPING", "STOP")
    )

    finished = threading.Event()

    def fake_execute(*_a, handle=None, **_k):
        time.sleep(0.15)
        assert handle is not None
        assert not handle.is_cancelled
        finished.set()
        out = DeleteTaskOutput(
            sampleId="S1",
            deleted=True,
            n_files_removed=0,
            started_at_utc="2020-01-01T00:00:00Z",
            finished_at_utc="2020-01-01T00:00:01Z",
            duration_ms=1,
            exit_code=0,
        )
        return ActionExecutionResult(result_code=0, output=out)

    runner = WorkerRunner(client, 1, "tok", heartbeat_seconds=0.05)
    with (
        patch("methyl_worker.runner.execute_task", side_effect=fake_execute),
        patch("methyl_worker.runner.validate_task_input"),
        patch("methyl_worker.runner.validate_task_output"),
    ):
        runner.run_once()

    assert finished.is_set()
    client.fail_task.assert_not_called()
    client.submit_result.assert_called_once()
