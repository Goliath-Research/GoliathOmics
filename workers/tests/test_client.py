"""Tests for methyl_worker REST client."""

from __future__ import annotations

import http.server
import ssl
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from methyl_worker.client import WorkflowRestClient, materialize_gateway_ca_file


def test_request_task_sends_arc_header(tmp_path) -> None:
    arc_env = tmp_path / "arc.env"
    arc_env.write_text('ARC_RESOURCE_ID="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.HybridCompute/machines/vm1"\n')
    client = WorkflowRestClient("http://test/v1", arc_resource_id=None)
    client.arc_resource_id = None
    with patch.dict("os.environ", {"METHYL_ARC_ENV": str(arc_env)}, clear=False):
        client2 = WorkflowRestClient("http://test/v1")
        assert client2.arc_resource_id is not None
        assert "machines/vm1" in client2.arc_resource_id

    with patch.object(WorkflowRestClient, "_post_json", return_value={"has_task": False}) as mock_post:
        client3 = WorkflowRestClient("http://test/v1", arc_resource_id="arc-123")
        client3.request_task(1, "tok")
    # Header is applied in _request_json via urlopen patch - test via _default_headers
    client4 = WorkflowRestClient("http://test/v1", arc_resource_id="arc-123")
    assert client4._default_headers() == {"X-Arc-Resource-Id": "arc-123"}


def test_request_task_empty() -> None:
    client = WorkflowRestClient("http://test/v1")
    with patch.object(
        client,
        "_post_json",
        return_value={"has_task": False, "desired_state": "DRAINING", "command": "DRAIN"},
    ):
        poll = client.request_task(1, "tok", "methyl-centroid")
    assert poll.claim is None
    assert poll.control.desired_state == "DRAINING"
    assert poll.control.command == "DRAIN"


def test_request_task_claim() -> None:
    client = WorkflowRestClient("http://test/v1")
    body = {
        "has_task": True,
        "node_execution_id": 42,
        "workflow_instance_id": 7,
        "action_name": "pipeline.centroid",
        "capability": "methyl-centroid",
        "node_key": "centroid_g1",
        "input_json": {"project": "/p.json", "tool": "MethylCentroid"},
        "desired_state": "ACTIVE",
        "command": "NONE",
    }
    with patch.object(client, "_post_json", return_value=body):
        poll = client.request_task(1, "tok")
    assert poll.claim is not None
    assert poll.claim.node_execution_id == 42
    assert poll.claim.input_json["project"] == "/p.json"
    assert poll.control.command == "NONE"


def test_heartbeat_ack() -> None:
    client = WorkflowRestClient("http://test/v1")
    with patch.object(
        client,
        "_post_json",
        return_value={"rows_updated": 1, "desired_state": "STOPPING", "command": "STOP"},
    ):
        ack = client.heartbeat(42, 1, "tok")
    assert ack.rows_updated == 1
    assert ack.control.command == "STOP"


def test_submit_result() -> None:
    client = WorkflowRestClient("http://test/v1")
    with patch.object(
        client,
        "_post_json",
        return_value={"accepted": True, "instance_status": "RUNNING", "next_ready_count": 3},
    ) as mock_post:
        ack = client.submit_result(42, 1, "tok", 0, {"status": "ok"})

    mock_post.assert_called_once()
    path, payload = mock_post.call_args[0]
    assert path == "/workers/tasks/42/submit"
    assert payload["result_code"] == 0
    assert payload["output_json"] == {"status": "ok"}
    assert ack.accepted is True
    assert ack.next_ready_count == 3


def test_post_json_http_error() -> None:
    client = WorkflowRestClient("http://test/v1")
    err = MagicMock()
    err.code = 401
    err.read.return_value = b"unauthorized"

    with patch("methyl_worker.client.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = HTTPError("http://x", 401, "nope", {}, err)
        with pytest.raises(RuntimeError, match="HTTP 401"):
            client.authenticate(1, "bad")


def _issue_test_cert(directory: Path) -> tuple[Path, Path]:
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    config = directory / "openssl.cnf"
    config.write_text(
        "\n".join(
            [
                "[req]",
                "distinguished_name = dn",
                "x509_extensions = v3",
                "prompt = no",
                "[dn]",
                "CN = 127.0.0.1",
                "[v3]",
                "subjectAltName = IP:127.0.0.1",
                "basicConstraints = critical,CA:TRUE",
                "keyUsage = critical,digitalSignature,keyEncipherment,keyCertSign",
                "extendedKeyUsage = serverAuth",
                "",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-days",
            "2",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-config",
            str(config),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _serve(cert: Path, key: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _clear_ca_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GOLIATH_GATEWAY_CA_PEM",
        "GOLIATH_GATEWAY_CA_FILE",
        "GOLIATH_ROOT",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_https_client_trusts_pinned_gateway_cert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cert, key = _issue_test_cert(tmp_path)
    server, port = _serve(cert, key)
    try:
        _clear_ca_env(monkeypatch)
        client = WorkflowRestClient(f"https://127.0.0.1:{port}/v1")
        with pytest.raises(RuntimeError, match="Trust the gateway certificate"):
            client.authenticate(1, "tok")

        monkeypatch.setenv("GOLIATH_GATEWAY_CA_FILE", str(cert))
        client.authenticate(1, "tok")

        _clear_ca_env(monkeypatch)
        monkeypatch.setenv("GOLIATH_ROOT", str(tmp_path))
        pem = cert.read_text(encoding="utf-8").replace("\n", "\\n")
        monkeypatch.setenv("GOLIATH_GATEWAY_CA_PEM", pem)
        written = materialize_gateway_ca_file()
        assert written is not None
        assert "BEGIN CERTIFICATE" in Path(written).read_text(encoding="utf-8")
        client.authenticate(1, "tok")
    finally:
        server.shutdown()
        server.server_close()
