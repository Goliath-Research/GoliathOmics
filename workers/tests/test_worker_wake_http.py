"""Gateway long-poll wakes on the idle loop (WORKER_WAKE_HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from methyl_worker.client import WorkflowRestClient
from methyl_worker.runner import WorkerRunner, _wake_wait_seconds


def _runner(poll_seconds: float = 5.0) -> tuple[WorkerRunner, MagicMock]:
    client = MagicMock(spec=WorkflowRestClient)
    client.session_expires_at.return_value = None
    client.session_opened_monotonic.return_value = 0.0
    return WorkerRunner(client, 7, "tok", poll_seconds=poll_seconds, heartbeat_seconds=60.0), client


def test_idle_wait_sleeps_when_wake_http_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_WAKE_HTTP", raising=False)
    runner, client = _runner()
    with patch("methyl_worker.runner.time.sleep") as sleep:
        runner.idle_wait()
    sleep.assert_called_once_with(5.0)
    client.wait_for_wake.assert_not_called()


def test_idle_wait_returns_on_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_WAKE_HTTP", "1")
    monkeypatch.setenv("WORKER_WAKE_POLL_SECONDS", "20")
    runner, client = _runner()
    client.wait_for_wake.return_value = True
    with patch("methyl_worker.runner.time.sleep") as sleep:
        runner.idle_wait()
    client.wait_for_wake.assert_called_once_with(7, "tok", timeout_seconds=20.0)
    sleep.assert_not_called()


def test_idle_wait_falls_back_to_poll_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_WAKE_HTTP", "1")
    runner, client = _runner()
    client.wait_for_wake.side_effect = RuntimeError("HTTP 409 /workers/wakes/wait: busy")
    with patch("methyl_worker.runner.time.sleep") as sleep:
        runner.idle_wait()
    sleep.assert_called_once_with(5.0)


def test_idle_wait_no_busy_loop_on_early_empty_return(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_WAKE_HTTP", "1")
    runner, client = _runner()
    client.wait_for_wake.return_value = False
    with patch("methyl_worker.runner.time.sleep") as sleep:
        runner.idle_wait()
    assert sleep.call_count == 1
    assert 0 < sleep.call_args.args[0] <= 5.0


def test_wake_wait_capped_by_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_WAKE_POLL_SECONDS", "300")
    assert _wake_wait_seconds(45.0) == 45.0
    monkeypatch.setenv("WORKER_WAKE_POLL_SECONDS", "bad")
    assert _wake_wait_seconds(60.0) == 30.0


def test_client_wait_for_wake_posts_session_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_WAKE_PROGRAM_ID", raising=False)
    client = WorkflowRestClient("https://gw.example/v1")
    with patch.object(client, "_ensure_worker_session") as ensure, patch.object(
        client, "_post_json", return_value={"wake": {"channel": "pool:x"}}
    ) as post:
        assert client.wait_for_wake(7, "tok", timeout_seconds=30) is True
    ensure.assert_called_once_with("tok")
    post.assert_called_once_with("/workers/wakes/wait", {"worker_id": 7, "timeout_seconds": 30.0})
    with patch.object(client, "_ensure_worker_session"), patch.object(
        client, "_post_json", return_value={"wake": None}
    ):
        assert client.wait_for_wake(7, "tok", timeout_seconds=30) is False
