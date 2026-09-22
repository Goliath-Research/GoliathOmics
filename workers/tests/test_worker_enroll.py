"""Unit tests for worker enroll helpers and CLI token write."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "workers"))

from methyl_worker.__main__ import (  # noqa: E402
    _default_api_base,
    _run_enroll_cli,
    _write_token_file,
    main,
)
from methyl_worker.capabilities import OMNIBUS_WILDCARD  # noqa: E402
from methyl_worker.client import WorkflowRestClient  # noqa: E402


class TokenFileTests(unittest.TestCase):
    def test_write_token_file_mode_600(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "worker-token"
            _write_token_file(path, 7, "abc123")
            text = path.read_text(encoding="utf-8")
            self.assertIn("WORKER_ID=7", text)
            self.assertIn("WORKER_TOKEN=abc123", text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class EnrollClientTests(unittest.TestCase):
    def test_enroll_posts_expected_body(self) -> None:
        client = WorkflowRestClient("http://gateway/v1", arc_resource_id="")
        with patch.object(client, "_post_json", return_value={"worker_id": 1, "worker_token": "t"}) as post:
            out = client.enroll("lambda", "vm-1", capabilities=["methyl-qc"])
        post.assert_called_once_with(
            "/workers/enroll",
            {
                "cluster_key": "lambda",
                "external_worker_key": "vm-1",
                "capabilities": ["methyl-qc"],
            },
        )
        self.assertEqual(out["worker_id"], 1)

    def test_enroll_403_fail_closed(self) -> None:
        from email.message import Message
        from io import BytesIO
        from urllib.error import HTTPError

        client = WorkflowRestClient("http://gateway/v1", arc_resource_id="")
        err = HTTPError(
            "http://gateway/v1/workers/enroll",
            403,
            "Forbidden",
            Message(),
            BytesIO(b'{"error":"not preregistered"}'),
        )
        with patch("methyl_worker.client.urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as ctx:
                client.enroll("lambda", "vm-1")
        msg = str(ctx.exception)
        self.assertIn("403", msg)
        self.assertIn("Fail closed", msg)
        self.assertIn("no SQL fallback", msg)


class ApiBasePrecedenceTests(unittest.TestCase):
    def test_worker_api_base_wins_over_methyl_api_base(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKER_API_BASE": "http://worker-first/v1",
                "METHYL_API_BASE": "http://methyl-second/v1",
            },
            clear=False,
        ):
            self.assertEqual(_default_api_base(), "http://worker-first/v1")

    def test_falls_back_to_methyl_api_base(self) -> None:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("WORKER_API_BASE", "METHYL_API_BASE")
        }
        env["METHYL_API_BASE"] = "http://methyl-only/v1"
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_default_api_base(), "http://methyl-only/v1")


class LegacyCliTests(unittest.TestCase):
    def test_once_flag_without_subcommand_is_poll(self) -> None:
        """systemd-style `methyl-worker --once` must not die as unrecognized args."""
        with patch("methyl_worker.__main__._run_poll_cli", return_value=0) as poll:
            rc = main(
                [
                    "--once",
                    "--worker-id",
                    "1",
                    "--worker-token",
                    "tok",
                    "--api-base",
                    "http://gw/v1",
                ]
            )
        self.assertEqual(rc, 0)
        poll.assert_called_once()
        ns = poll.call_args[0][0]
        self.assertEqual(ns.command, "poll")
        self.assertTrue(ns.once)
        self.assertEqual(ns.worker_id, 1)

    def test_explicit_enroll_subcommand(self) -> None:
        with patch("methyl_worker.__main__._run_enroll_cli", return_value=0) as enroll:
            rc = main(["enroll", "--cluster", "lambda", "--key", "vm-1"])
        self.assertEqual(rc, 0)
        enroll.assert_called_once()


class EnrollCliTests(unittest.TestCase):
    def test_enroll_cli_writes_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "token"
            args = MagicMock(
                cluster_key="lambda",
                external_worker_key="vm-1",
                api_base="http://gw/v1",
                capabilities_json="",
                token_file=str(token_path),
                env_file="",
            )
            with patch("methyl_worker.__main__.WorkflowRestClient") as cls, patch(
                "methyl_worker.__main__.resolve_worker_capabilities",
                return_value=["methylgrapher.wgbs_align", "parabricks.fq2bam"],
            ) as resolve:
                cls.return_value.enroll.return_value = {
                    "worker_id": 3,
                    "worker_token": "secret",
                    "cluster_key": "lambda",
                    "external_worker_key": "vm-1",
                }
                rc = _run_enroll_cli(args)
            self.assertEqual(rc, 0)
            resolve.assert_called_once_with()
            cls.return_value.enroll.assert_called_once_with(
                "lambda",
                "vm-1",
                capabilities=["methylgrapher.wgbs_align", "parabricks.fq2bam"],
            )
            self.assertEqual(
                token_path.read_text(encoding="utf-8"),
                "WORKER_ID=3\nWORKER_TOKEN=secret\n",
            )

    def test_enroll_cli_refuses_empty_probe(self) -> None:
        args = MagicMock(
            cluster_key="lambda",
            external_worker_key="vm-1",
            api_base="http://gw/v1",
            capabilities_json="",
            token_file="/tmp/unused-token",
            env_file="",
        )
        with patch("methyl_worker.__main__.WorkflowRestClient") as cls, patch(
            "methyl_worker.__main__.resolve_worker_capabilities",
            return_value=[],
        ):
            rc = _run_enroll_cli(args)
        self.assertEqual(rc, 2)
        cls.return_value.enroll.assert_not_called()

    def test_enroll_cli_refuses_probed_omnibus(self) -> None:
        args = MagicMock(
            cluster_key="lambda",
            external_worker_key="vm-1",
            api_base="http://gw/v1",
            capabilities_json="",
            token_file="/tmp/unused-token",
            env_file="",
        )
        with patch("methyl_worker.__main__.WorkflowRestClient") as cls, patch(
            "methyl_worker.__main__.resolve_worker_capabilities",
            return_value=[OMNIBUS_WILDCARD],
        ):
            rc = _run_enroll_cli(args)
        self.assertEqual(rc, 2)
        cls.return_value.enroll.assert_not_called()

    def test_enroll_cli_allows_explicit_omnibus_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "token"
            args = MagicMock(
                cluster_key="lambda",
                external_worker_key="vm-1",
                api_base="http://gw/v1",
                capabilities_json='["*"]',
                token_file=str(token_path),
                env_file="",
            )
            with patch("methyl_worker.__main__.WorkflowRestClient") as cls:
                cls.return_value.enroll.return_value = {
                    "worker_id": 4,
                    "worker_token": "tok",
                }
                rc = _run_enroll_cli(args)
            self.assertEqual(rc, 0)
            cls.return_value.enroll.assert_called_once_with(
                "lambda", "vm-1", capabilities=["*"]
            )


if __name__ == "__main__":
    unittest.main()
