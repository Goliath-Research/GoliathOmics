"""HTTP client for the workflow middle-tier REST API (contracts/openapi.yaml)."""

from __future__ import annotations

import json
import logging
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskClaim:
    """One claimed READY task from POST /workers/tasks/request."""

    node_execution_id: int
    workflow_instance_id: int
    action_name: str
    capability: str
    input_json: Dict[str, Any]
    node_key: str = ""
    attempt_no: int = 1
    iteration_no: int = 0


@dataclass(frozen=True)
class WorkerControlAck:
    """Fleet control echoed from claim/heartbeat (wf.worker.desired_state)."""

    desired_state: str = "ACTIVE"
    command: str = "NONE"  # NONE | DRAIN | STOP


@dataclass(frozen=True)
class TaskPollResult:
    """Idle or claimed poll from POST /workers/tasks/request."""

    claim: Optional[TaskClaim]
    control: WorkerControlAck = WorkerControlAck()


@dataclass(frozen=True)
class HeartbeatAck:
    rows_updated: int
    control: WorkerControlAck = WorkerControlAck()


@dataclass(frozen=True)
class SubmitAck:
    accepted: bool
    instance_status: str
    next_ready_count: int


def _normalize_pem(raw: str) -> str:
    text = raw.strip()
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    if "-----BEGIN CERTIFICATE-----" not in text:
        raise RuntimeError("GOLIATH_GATEWAY_CA_PEM does not contain a PEM certificate")
    if not text.endswith("\n"):
        text += "\n"
    return text


def materialize_gateway_ca_file() -> Optional[str]:
    """Write GOLIATH_GATEWAY_CA_PEM to a file, or return GOLIATH_GATEWAY_CA_FILE.

    The returned file is an extra trust anchor for the gateway, loaded in
    addition to the system CA store.
    """
    pem = os.environ.get("GOLIATH_GATEWAY_CA_PEM", "")
    explicit = os.environ.get("GOLIATH_GATEWAY_CA_FILE", "").strip()
    if pem.strip():
        text = _normalize_pem(pem)
        if explicit:
            dest = Path(explicit)
        else:
            root = os.environ.get("GOLIATH_ROOT", "").strip()
            dest = Path(root) / "env" / "gateway-ca.pem" if root else Path("/etc/methyl/gateway-ca.pem")
        dest.parent.mkdir(parents=True, exist_ok=True)
        current = dest.read_text(encoding="utf-8") if dest.is_file() else ""
        if current != text:
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            try:
                os.chmod(tmp, 0o644)
            except OSError as exc:
                # geesefs /work rejects chmod. The writer can still read the
                # trust anchor it just created.
                logger.warning("chmod 0644 rejected for %s: %s", tmp, exc)
            tmp.replace(dest)
        return str(dest)
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise RuntimeError(f"GOLIATH_GATEWAY_CA_FILE is not a file: {explicit}")
        return str(path)
    return None


def gateway_ssl_context() -> ssl.SSLContext:
    """Default trust store plus the pinned gateway certificate, when one is set.

    REQUESTS_CA_BUNDLE and CURL_CA_BUNDLE are loaded as additional anchors so
    a combined bundle (system CAs plus the gateway cert) is honored. They do
    not replace the default store unless SSL_CERT_FILE is already set in the
    environment, which OpenSSL applies inside create_default_context.
    """
    ctx = ssl.create_default_context()
    extras: list[str] = []
    pinned = materialize_gateway_ca_file()
    if pinned:
        extras.append(pinned)
    for key in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        value = os.environ.get(key, "").strip()
        if value and value not in extras and Path(value).is_file():
            extras.append(value)
    for path in extras:
        try:
            ctx.load_verify_locations(cafile=path)
        except (OSError, ssl.SSLError) as exc:
            raise RuntimeError(f"gateway CA bundle is not a readable certificate file: {path}") from exc
    return ctx


def _load_arc_resource_id() -> Optional[str]:
    explicit = os.environ.get("ARC_RESOURCE_ID", "").strip()
    if explicit:
        return explicit
    arc_env = Path(os.environ.get("METHYL_ARC_ENV", "/etc/methyl/arc.env"))
    if not arc_env.is_file():
        return None
    for line in arc_env.read_text(encoding="utf-8").splitlines():
        if line.startswith("ARC_RESOURCE_ID="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            return val or None
    return None


def _control_from_body(body: Dict[str, Any]) -> WorkerControlAck:
    return WorkerControlAck(
        desired_state=str(body.get("desired_state") or "ACTIVE"),
        command=str(body.get("command") or "NONE"),
    )


class WorkflowRestClient:
    """REST-only workflow worker client; no direct database access."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout_seconds: float = 120.0,
        arc_resource_id: Optional[str] = None,
    ) -> None:
        raw = base_url or os.environ.get("METHYL_API_BASE", "http://localhost:8080/v1")
        self.base_url = raw.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.arc_resource_id = arc_resource_id if arc_resource_id is not None else _load_arc_resource_id()

    def _default_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.arc_resource_id:
            headers["X-Arc-Resource-Id"] = self.arc_resource_id
        return headers

    def _request_json(self, path: str, *, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self._default_headers()
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method=method)
        try:
            if req.type == "https":
                response_cm = urlopen(req, timeout=self.timeout_seconds, context=gateway_ssl_context())
            else:
                response_cm = urlopen(req, timeout=self.timeout_seconds)
            with response_cm as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return {}
                return json.loads(body)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403:
                raise RuntimeError(
                    f"HTTP 403 {path}: enroll refused (portal IP/key not preregistered). "
                    f"Fail closed — no SQL fallback. {detail}"
                ) from exc
            raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
        except URLError as exc:
            hint = ""
            reason_text = str(getattr(exc, "reason", exc))
            if "CERTIFICATE_VERIFY_FAILED" in reason_text or "certificate verify failed" in reason_text:
                hint = (
                    " Trust the gateway certificate with GOLIATH_GATEWAY_CA_FILE or "
                    "GOLIATH_GATEWAY_CA_PEM (the RunPod secret of that name)."
                )
            raise RuntimeError(f"Request failed {path}: {exc}.{hint}") from exc

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json(path, method="POST", payload=payload)

    def _get_json(self, path: str) -> Dict[str, Any]:
        return self._request_json(path, method="GET")

    def authenticate(self, worker_id: int, worker_token: str) -> None:
        self._post_json(
            "/workers/authenticate",
            {"worker_id": worker_id, "worker_token": worker_token},
        )

    def enroll(
        self,
        cluster_key: str,
        external_worker_key: str,
        *,
        capabilities: Optional[list[Any]] = None,
        arc_resource_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /workers/enroll — mint worker_id + token from portal IP allowlist."""
        payload: Dict[str, Any] = {
            "cluster_key": cluster_key,
            "external_worker_key": external_worker_key,
        }
        if capabilities is not None:
            payload["capabilities"] = capabilities
        rid = arc_resource_id if arc_resource_id is not None else self.arc_resource_id
        if rid:
            payload["arc_resource_id"] = rid
        return self._post_json("/workers/enroll", payload)

    def request_task(
        self,
        worker_id: int,
        worker_token: str,
        capability: Optional[str] = None,
        *,
        max_lease_seconds: Optional[int] = None,
    ) -> TaskPollResult:
        payload: Dict[str, Any] = {
            "worker_id": worker_id,
            "worker_token": worker_token,
            "max_lease_seconds": max_lease_seconds
            or int(os.environ.get("WORKER_LEASE_SECONDS", "300")),
        }
        if capability:
            payload["capability"] = capability

        body = self._post_json("/workers/tasks/request", payload)
        control = _control_from_body(body)
        # Empty polls may omit has_task or return null node_execution_id with a
        # desired_state ACK row — treat as idle, not a crash.
        if not body.get("has_task") or body.get("node_execution_id") in (None, ""):
            return TaskPollResult(claim=None, control=control)

        inp = body.get("input_json") or {}
        if isinstance(inp, str):
            inp = json.loads(inp) if inp else {}

        claim = TaskClaim(
            node_execution_id=int(body["node_execution_id"]),
            workflow_instance_id=int(body.get("workflow_instance_id") or 0),
            action_name=str(body.get("action_name") or ""),
            capability=str(body.get("capability") or ""),
            input_json=inp,
            node_key=str(body.get("node_key") or ""),
            attempt_no=int(body.get("attempt_no") or 1),
            iteration_no=int(body.get("iteration_no") or 0),
        )
        return TaskPollResult(claim=claim, control=control)

    def submit_result(
        self,
        node_execution_id: int,
        worker_id: int,
        worker_token: str,
        result_code: int,
        output_json: Optional[Dict[str, Any]] = None,
    ) -> SubmitAck:
        payload: Dict[str, Any] = {
            "worker_id": worker_id,
            "worker_token": worker_token,
            "result_code": result_code,
        }
        if output_json is not None:
            payload["output_json"] = output_json

        body = self._post_json(f"/workers/tasks/{node_execution_id}/submit", payload)
        return SubmitAck(
            accepted=bool(body.get("accepted", True)),
            instance_status=str(body.get("instance_status") or ""),
            next_ready_count=int(body.get("next_ready_count") or 0),
        )

    def heartbeat(
        self,
        node_execution_id: int,
        worker_id: int,
        worker_token: str,
        *,
        extend_seconds: Optional[int] = None,
    ) -> HeartbeatAck:
        body = self._post_json(
            f"/workers/tasks/{node_execution_id}/heartbeat",
            {
                "worker_id": worker_id,
                "worker_token": worker_token,
                "extend_seconds": extend_seconds
                or int(os.environ.get("WORKER_LEASE_SECONDS", "300")),
            },
        )
        return HeartbeatAck(
            rows_updated=int(body.get("rows_updated") or 0),
            control=_control_from_body(body),
        )

    def fail_task(
        self,
        node_execution_id: int,
        worker_id: int,
        worker_token: str,
        error_code: int,
        error_message: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "worker_id": worker_id,
            "worker_token": worker_token,
            "error_code": error_code,
        }
        if error_message:
            payload["error_message"] = error_message
        self._post_json(f"/workers/tasks/{node_execution_id}/fail", payload)

    def create_workflow_instance(
        self,
        workflow_version_id: int,
        context_json: Optional[Dict[str, Any]] = None,
        *,
        start: bool = True,
    ) -> Dict[str, Any]:
        body = self._post_json(
            "/workflows/instances",
            {
                "workflow_version_id": workflow_version_id,
                "context_json": context_json or {},
                "start": start,
            },
        )
        return body

    def get_workflow_instance(self, instance_id: int) -> Dict[str, Any]:
        return self._get_json(f"/workflows/instances/{instance_id}")

    def list_actions(self) -> list[Dict[str, Any]]:
        body = self._get_json("/actions")
        return list(body.get("actions") or [])
