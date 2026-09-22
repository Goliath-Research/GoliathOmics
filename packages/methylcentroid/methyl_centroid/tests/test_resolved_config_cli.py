"""Centroid CLI forwards --resolved-config to project resolver entrypoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from methyl_centroid import cli


def test_main_forwards_resolved_config_to_run_centroid_for_one_group(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    resolved = tmp_path / "resolved.json"
    resolved.write_text("{}", encoding="utf-8")
    seen: dict[str, Path | None] = {}

    def fake_run_one(
        project_path,
        group,
        step_override_path,
        *,
        output_dir=None,
        chromosome=None,
        context=None,
        resolved_config_path=None,
    ):
        seen["resolved_config_path"] = resolved_config_path

    with patch.object(cli, "run_centroid_for_one_group", fake_run_one):
        with patch.object(cli, "group_token_requests_all_groups", return_value=False):
            with patch.object(cli, "setup_logging"):
                with patch.object(
                    cli.sys,
                    "argv",
                    [
                        "methyl-centroid",
                        "--project",
                        str(project),
                        "--group",
                        "0",
                        "--resolved-config",
                        str(resolved),
                    ],
                ):
                    cli.main()

    assert str(seen.get("resolved_config_path")) == str(resolved)
