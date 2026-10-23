"""Poll loop: claim tasks, execute handlers, submit results; honor fleet desired_state."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
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


def _session_ttl_seconds(heartbeat_seconds: float) -> float:
    miss = float(os.environ.get("WORKER_SESSION_MISS_FACTOR", "3"))
    return heartbeat_seconds * miss


def _is_stale_session_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "worker session" in msg
        or "invalid or expired worker session" in msg
        or "http 401" in msg
        or "50003" in msg
    )


class WorkerRunner:
    def __init__(
        self,
        client: WorkflowRestClient,
        worker_id: int,
        identity_token: str,
        capability: Optional[str] = None,
        *,
        poll_seconds: float = 5.0,
        heartbeat_seconds: float = 60.0,
    ) -> None:
        self.client = client
        self.worker_id = worker_id
        self.identity_token = identity_token
        self.capability = capability
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._desired_state = "ACTIVE"
        self._session_opened_at: Optional[datetime] = None
        self._open_session()

    def _open_session(self) -> None:
        self.client.open_session(self.identity_token)
        self._session_opened_at = datetime.now(timezone.utc)

    def _refresh_session_if_needed(self) -> None:
        expires = self.client.session_expires_at()
        refresh_margin = timedelta(seconds=self.heartbeat_seconds)
        now = datetime.now(timezone.utc)
        if expires is None:
            # No expires_at_utc. TTL is measured from the open, not from this
            # call: now+TTL compared with now+one heartbeat is never due.
            opened = self._session_opened_at
            if opened is None:
                opened = now
                self._session_opened_at = opened
            expires = opened + timedelta(seconds=_session_ttl_seconds(self.heartbeat_seconds))
        if now + refresh_margin >= expires:
            logger.info("Refreshing worker gateway session before expiry")
            self._open_session()

    def _with_session_retry(self, fn):
        try:
            return fn()
        except Exception as exc:
            if not _is_stale_session_error(exc):
                raise
            logger.warning("Worker session rejected; opening a new session: %s", exc)
            self._open_session()
            return fn()

    def run_once(self) -> bool:
        """Poll once; return True if a task was processed."""
        self._refresh_session_if_needed()
        poll = self._with_session_retry(
            lambda: self.client.request_task(self.worker_id, self.identity_token, self.capability)
        )
        self._apply_control(poll.control, in_flight=None)
        if poll.control.command in ("DRAIN", "STOP") and poll.claim is None:
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
                    self._refresh_session_if_needed()
                    ack = self._with_session_retry(
                        lambda: self.client.heartbeat(
                            ne_id, self.worker_id, self.identity_token
                        )
                    )
                    self._apply_control(ack.control, in_flight=handle, action_name=claim.action_name)
                    if ack.control.command == "DRAIN" and ctrl.can_pause:
                        handle.request_pause()
                    elif ack.control.command == "NONE" and ctrl.can_continue:
                        handle.clear_pause()
                except Exception:
                    logger.exception("Heartbeat failed for task %s", ne_id)

        try:
            self._refresh_session_if_needed()
            ack0 = self._with_session_retry(
                lambda: self.client.heartbeat(ne_id, self.worker_id, self.identity_token)
            )
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
            ack = self._with_session_retry(
                lambda: self.client.submit_result(
                    ne_id,
                    self.worker_id,
                    self.identity_token,
                    execution.result_code,
                    output_payload,
                )
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
            self._with_session_retry(
                lambda: self.client.fail_task(
                    ne_id,
                    self.worker_id,
                    self.identity_token,
                    WORKER_STOPPED_ERROR_CODE,
                    f"{WORKER_STOPPED_ERROR_NAME}: {exc}",
                )
            )
        except TaskValidationError as exc:
            logger.error("Task %s schema validation failed: %s", ne_id, exc)
            self._with_session_retry(
                lambda: self.client.fail_task(
                    ne_id,
                    self.worker_id,
                    self.identity_token,
                    TASK_VALIDATION_ERROR_CODE,
                    str(exc),
                )
            )
        except Exception as exc:
            logger.exception("Task %s failed", ne_id)
            self._with_session_retry(
                lambda: self.client.submit_result(
                    ne_id,
                    self.worker_id,
                    self.identity_token,
                    -1,
                    {"error": str(exc), "capability": claim.capability},
                )
            )
        finally:
            stop_hb.set()
            hb_thread.join(timeout=1.0)


def _output_payload(output: BaseModel) -> dict:
    return output.model_dump(mode="json")
