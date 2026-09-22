"""Cancelable execution handle for agnostic worker stop (catalog.control.can_stop)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator, List, Optional

logger = logging.getLogger(__name__)

WORKER_STOPPED_ERROR_CODE = 4099
WORKER_STOPPED_ERROR_NAME = "WORKER_STOPPED"

_current_handle: ContextVar[Optional["ExecutionHandle"]] = ContextVar(
    "methyl_worker_execution_handle", default=None
)


class WorkerStoppedError(RuntimeError):
    """Raised when the operator stops an in-flight task (desired_state=STOPPING)."""

    error_code = WORKER_STOPPED_ERROR_CODE
    error_name = WORKER_STOPPED_ERROR_NAME

    def __init__(self, message: str = "Worker stopped by operator") -> None:
        super().__init__(message)


class ExecutionHandle:
    """Shared cancel signal + optional child process registry for STOP."""

    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.pause = threading.Event()  # cooperative pause (catalog.can_pause)
        self._lock = threading.Lock()
        self._procs: List[subprocess.Popen] = []

    @property
    def is_cancelled(self) -> bool:
        return self.cancel.is_set()

    def request_cancel(self) -> None:
        self.cancel.set()
        self.kill_children()

    def request_pause(self) -> None:
        self.pause.set()

    def clear_pause(self) -> None:
        self.pause.clear()

    def register_process(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.append(proc)
        if self.is_cancelled:
            self._kill_one(proc)

    def kill_children(self) -> None:
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            self._kill_one(proc)

    @staticmethod
    def _kill_one(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def current_handle() -> Optional[ExecutionHandle]:
    return _current_handle.get()


@contextmanager
def bind_execution_handle(handle: Optional[ExecutionHandle]) -> Generator[Optional[ExecutionHandle], None, None]:
    token = _current_handle.set(handle)
    try:
        yield handle
    finally:
        _current_handle.reset(token)


def run_cancellable(
    cmd: List[str],
    *,
    handle: Optional[ExecutionHandle] = None,
    poll_seconds: float = 0.25,
) -> subprocess.CompletedProcess[str]:
    """Popen + process-group wait; honors ``handle.cancel`` when set.

    Uses ``communicate(timeout=…)`` so stdout/stderr pipes are drained while
    waiting. A bare ``poll()`` loop with ``PIPE`` deadlocks once the child fills
    the OS pipe buffer (~64 KiB) — never use that pattern here.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    active = handle or current_handle()
    if active is not None:
        active.register_process(proc)
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=poll_seconds)
                # Cancel may kill the child while communicate() is waiting; that
                # completes the wait without TimeoutExpired — still treat as stop.
                if active is not None and active.is_cancelled:
                    raise WorkerStoppedError(f"Stopped while running: {cmd[0]}")
                return subprocess.CompletedProcess(
                    cmd, int(proc.returncode or 0), stdout or "", stderr or ""
                )
            except subprocess.TimeoutExpired:
                if active is not None and active.is_cancelled:
                    active.kill_children()
                    try:
                        proc.communicate(timeout=5)
                    except Exception:
                        ExecutionHandle._kill_one(proc)
                    raise WorkerStoppedError(f"Stopped while running: {cmd[0]}") from None
    finally:
        if proc.poll() is None:
            ExecutionHandle._kill_one(proc)
