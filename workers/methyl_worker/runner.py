"""Poll loop: claim tasks, execute handlers, submit results; honor fleet desired_state."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from pydantic import BaseModel

from .action_catalog import control_for
from .action_execution import ActionExecutionResult
from .client import TaskClaim, WorkflowRestClient, WorkerControlAck
from .execution_handle import (
    WORKER_STOPPED_ERROR_CODE,
    WORKER_STOPPED_ERROR_NAME,
    ExecutionHandle,
    WorkerStoppedError,
)
from .handlers import execute_task
from .task_validation import (
    TASK_VALIDATION_ERROR_CODE,
    TaskValidationError,
    validate_task_input,
    validate_task_output,
)

logger = logging.getLogger(__name__)


class WorkerRunner:
    def __init__(
        self,
        client: WorkflowRestClient,
        worker_id: int,
        worker_token: str,
        capability: Optional[str] = None,
        *,
        poll_seconds: float = 5.0,
        heartbeat_seconds: float = 60.0,
    ) -> None:
        self.client = client
        self.worker_id = worker_id
        self.worker_token = worker_token
        self.capability = capability
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._desired_state = "ACTIVE"

    def run_once(self) -> bool:
        """Poll once; return True if a task was processed."""
        poll = self.client.request_task(self.worker_id, self.worker_token, self.capability)
        self._apply_control(poll.control, in_flight=None)
        if poll.control.command in ("DRAIN", "STOP") and poll.claim is None:
            # Draining / stopping with no claim — idle wait.
            return False
        if poll.claim is None:
            return False
        self._process_claim(poll.claim)
        return True

    def run_forever(self) -> None:
        while True:
            if not self.run_once():
                time.sleep(self.poll_seconds)

    def _apply_control(
        self,
        control: WorkerControlAck,
        *,
        in_flight: Optional[ExecutionHandle],
        action_name: str = "",
    ) -> None:
        self._desired_state = control.desired_state
        if control.command != "STOP" or in_flight is None:
            return
        ctrl = control_for(action_name) if action_name else control_for("")
        if ctrl.can_stop:
            logger.warning(
                "Operator STOP for action %s (desired_state=%s)",
                action_name or "?",
                control.desired_state,
            )
            in_flight.request_cancel()
        else:
            logger.warning(
                "STOP requested but catalog.control.can_stop=false for %s; "
                "draining after natural completion",
                action_name or "?",
            )

    def _process_claim(self, claim: TaskClaim) -> None:
        ne_id = claim.node_execution_id
        stop_hb = threading.Event()
        handle = ExecutionHandle()
        ctrl = control_for(claim.action_name)

        def heartbeat_loop() -> None:
            while not stop_hb.wait(self.heartbeat_seconds):
                try:
                    ack = self.client.heartbeat(ne_id, self.worker_id, self.worker_token)
                    self._apply_control(ack.control, in_flight=handle, action_name=claim.action_name)
                    if ack.control.command == "DRAIN" and ctrl.can_pause:
                        handle.request_pause()
                    elif ack.control.command == "NONE" and ctrl.can_continue:
                        handle.clear_pause()
                except Exception:
                    logger.exception("Heartbeat failed for task %s", ne_id)

        try:
            ack0 = self.client.heartbeat(ne_id, self.worker_id, self.worker_token)
            self._apply_control(ack0.control, in_flight=handle, action_name=claim.action_name)
        except Exception:
            logger.exception("Initial heartbeat failed for task %s", ne_id)

        hb_thread = threading.Thread(target=heartbeat_loop, name=f"hb-{ne_id}", daemon=True)
        hb_thread.start()

        try:
            validate_task_input(claim.action_name, claim.capability, claim.input_json)
            execution = execute_task(
                claim.capability,
                claim.action_name,
                claim.input_json,
                handle=handle,
            )
            validate_task_output(claim.action_name, claim.capability, execution.output)
            output_payload = _output_payload(execution.output)
            ack = self.client.submit_result(
                ne_id,
                self.worker_id,
                self.worker_token,
                execution.result_code,
                output_payload,
            )
            logger.info(
                "Submitted task %s (%s) result_code=%s accepted=%s instance=%s ready=%s",
                ne_id,
                claim.capability,
                execution.result_code,
                ack.accepted,
                ack.instance_status,
                ack.next_ready_count,
            )
        except WorkerStoppedError as exc:
            logger.warning("Task %s stopped by operator: %s", ne_id, exc)
            self.client.fail_task(
                ne_id,
                self.worker_id,
                self.worker_token,
                WORKER_STOPPED_ERROR_CODE,
                f"{WORKER_STOPPED_ERROR_NAME}: {exc}",
            )
        except TaskValidationError as exc:
            logger.error("Task %s schema validation failed: %s", ne_id, exc)
            self.client.fail_task(
                ne_id,
                self.worker_id,
                self.worker_token,
                TASK_VALIDATION_ERROR_CODE,
                str(exc),
            )
        except Exception as exc:
            logger.exception("Task %s failed", ne_id)
            self.client.submit_result(
                ne_id,
                self.worker_id,
                self.worker_token,
                -1,
                {"error": str(exc), "capability": claim.capability},
            )
        finally:
            stop_hb.set()
            hb_thread.join(timeout=1.0)


def _output_payload(output: BaseModel) -> dict:
    return output.model_dump(mode="json")
