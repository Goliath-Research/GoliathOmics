"""Tests for the shared CLI resolved-config helper.

``cli_resolved_config`` is imported by ~9 package CLIs to decide between the
worker ``--resolved-config`` path and the standalone ``--project`` path. A
routing regression here silently changes how every CLI reads its tool config.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_utils.cli_resolved_config import (
    add_resolved_config_argument,
    read_json_object,
    resolve_cli_step_config,
)


# --------------------------------------------------------------------------- #
# read_json_object
# --------------------------------------------------------------------------- #
def test_read_json_object_none_for_empty_input():
    assert read_json_object(None) is None
    assert read_json_object("") is None


def test_read_json_object_missing_file_returns_none(tmp_path: Path):
    assert read_json_object(tmp_path / "does_not_exist.json") is None


def test_read_json_object_reads_object(tmp_path: Path):
    p = tmp_path / "obj.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert read_json_object(p) == {"a": 1}


def test_read_json_object_rejects_non_object(tmp_path: Path):
    p = tmp_path / "arr.json"
    p.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object expected"):
        read_json_object(p)


# --------------------------------------------------------------------------- #
# resolve_cli_step_config routing
# --------------------------------------------------------------------------- #
def test_resolve_cli_step_config_worker_path(tmp_path: Path):
    """--resolved-config path: baked slice used, no project consulted."""
    cfg = tmp_path / "resolved.json"
    cfg.write_text(json.dumps({"top": 150}), encoding="utf-8")
    out = resolve_cli_step_config(
        "enricher",
        project=None,
        resolved_config_path=cfg,
    )
    assert out == {"top": 150}


def test_resolve_cli_step_config_worker_path_applies_step_override(tmp_path: Path):
    cfg = tmp_path / "resolved.json"
    cfg.write_text(json.dumps({"top": 150, "cutoff": 0.05}), encoding="utf-8")
    override = tmp_path / "override.json"
    override.write_text(json.dumps({"top": 10}), encoding="utf-8")
    out = resolve_cli_step_config(
        "enricher",
        project=None,
        resolved_config_path=cfg,
        step_override_path=override,
    )
    assert out["top"] == 10
    assert out["cutoff"] == 0.05


def test_resolve_cli_step_config_requires_project_or_resolved():
    with pytest.raises(ValueError, match="--project or --resolved-config is required"):
        resolve_cli_step_config("enricher", project=None)


def test_resolve_cli_step_config_standalone_project_path(monkeypatch):
    """--project path routes through resolve_for_project (env/profile fallback)."""
    captured = {}

    def _fake_resolve_for_project(action_key, project, *, step_override=None):
        captured["action_key"] = action_key
        captured["project"] = project
        captured["step_override"] = step_override
        return {"resolved": "from_project"}

    monkeypatch.setattr(
        "methyl_utils.cli_resolved_config.resolve_for_project",
        _fake_resolve_for_project,
    )

    class _Project:
        pass

    proj = _Project()
    out = resolve_cli_step_config("mapper", project=proj)
    assert out == {"resolved": "from_project"}
    assert captured["action_key"] == "mapper"
    assert captured["project"] is proj


def test_add_resolved_config_argument_registers_flag():
    import argparse

    parser = argparse.ArgumentParser()
    add_resolved_config_argument(parser, help_suffix="Used by methyl-mapper.")
    ns = parser.parse_args(["--resolved-config", "/tmp/baked.json"])
    assert ns.resolved_config == "/tmp/baked.json"
