"""Unit tests for the Rollouts deployment reporter. No network."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import report_rollouts_deploy as reporter


class ReporterContractTest(unittest.TestCase):
    def test_list_filter_matches_contract(self) -> None:
        self.assertEqual(
            reporter.list_filter("production-work", "goliathomics", "abc123"),
            'environment = "environments/production-work" AND '
            'deploy_version = "abc123" AND '
            'service = "services/goliathomics"',
        )

    def test_already_exists_accepts_409_and_code(self) -> None:
        self.assertTrue(reporter.is_already_exists(409, {}))
        self.assertTrue(reporter.is_already_exists(400, {"code": "already_exists"}))
        self.assertFalse(reporter.is_already_exists(500, {"code": "unavailable"}))

    def test_select_reusable_matches_actor_and_terminal_event(self) -> None:
        deployments = [
            {
                "name": "deployments/other",
                "events": [{"actor": "other", "started": {}}],
            },
            {
                "name": "deployments/ours",
                "events": [
                    {"actor": "actor-1", "started": {}},
                    {"actor": "actor-1", "completed": {"succeeded": {}}},
                ],
            },
        ]
        name, terminal = reporter.select_reusable(deployments, "actor-1")
        self.assertEqual(name, "deployments/ours")
        self.assertTrue(terminal)

    def test_select_reusable_open_deployment(self) -> None:
        deployments = [
            {
                "name": "deployments/ours",
                "events": [{"actor": "actor-1", "started": {}}],
            }
        ]
        name, terminal = reporter.select_reusable(deployments, "actor-1")
        self.assertEqual(name, "deployments/ours")
        self.assertFalse(terminal)

    def test_select_reusable_ignores_other_actors(self) -> None:
        deployments = [
            {
                "name": "deployments/theirs",
                "events": [{"actor": "someone-else", "completed": {"succeeded": {}}}],
            }
        ]
        self.assertEqual(reporter.select_reusable(deployments, "actor-1"), (None, False))

    def test_aborted_event_is_terminal(self) -> None:
        deployments = [
            {
                "name": "deployments/ours",
                "events": [{"actor": "actor-1", "aborted": {}}],
            }
        ]
        _name, terminal = reporter.select_reusable(deployments, "actor-1")
        self.assertTrue(terminal)

    def test_completion_bodies(self) -> None:
        self.assertEqual(
            reporter.completion_body("success", ""),
            {"completed": {"succeeded": {}}},
        )
        self.assertEqual(
            reporter.completion_body("failure", "promote exited 1"),
            {"completed": {"failed": {"message": "promote exited 1"}}},
        )
        self.assertEqual(reporter.completion_body("cancelled", ""), {"aborted": {}})

    def test_unknown_outcome_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            reporter.completion_body("skipped", "")

    def test_finish_skips_second_completed_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "name": "deployments/ours",
                        "terminal": False,
                        "version": "abc",
                        "actor": "actor-1",
                    },
                    handle,
                )
            env = {
                "ROLLOUTS_STATE_FILE": state_file,
                "CURSOR_API_KEY": "test-key",
                "CHANGE_MONITOR_ENV": "production-work",
                "CHANGE_MONITOR_SERVICE": "goliathomics",
                "DEPLOY_SOURCE_URI": "https://github.com/Goliath-Research/GoliathOmics",
                "DEPLOY_ACTOR": "actor-1",
                "DEPLOY_OUTCOME": "success",
            }
            listed = [
                {
                    "name": "deployments/ours",
                    "events": [
                        {"actor": "actor-1", "started": {}},
                        {"actor": "actor-1", "completed": {"succeeded": {}}},
                    ],
                }
            ]
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(reporter, "exchange_token", return_value="token"):
                    with mock.patch.object(reporter, "list_deployments", return_value=listed):
                        with mock.patch.object(reporter, "factory_post") as post:
                            reporter.cmd_finish()
            post.assert_not_called()
            with open(state_file, encoding="utf-8") as handle:
                self.assertTrue(json.load(handle)["terminal"])

    def test_finish_appends_succeeded_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, "state.json")
            with open(state_file, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "name": "deployments/ours",
                        "terminal": False,
                        "version": "abc",
                        "actor": "actor-1",
                    },
                    handle,
                )
            env = {
                "ROLLOUTS_STATE_FILE": state_file,
                "CURSOR_API_KEY": "test-key",
                "CHANGE_MONITOR_ENV": "production-work",
                "CHANGE_MONITOR_SERVICE": "goliathomics",
                "DEPLOY_SOURCE_URI": "https://github.com/Goliath-Research/GoliathOmics",
                "DEPLOY_ACTOR": "actor-1",
                "DEPLOY_OUTCOME": "success",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(reporter, "exchange_token", return_value="token"):
                    with mock.patch.object(reporter, "list_deployments", return_value=[]):
                        with mock.patch.object(
                            reporter, "factory_post", return_value=(200, {})
                        ) as post:
                            reporter.cmd_finish()
            post.assert_called_once_with(
                "token",
                "AppendDeploymentEvent",
                {
                    "name": "deployments/ours",
                    "event": {
                        "completed": {"succeeded": {}},
                        "actor": "actor-1",
                    },
                },
            )


if __name__ == "__main__":
    unittest.main()
