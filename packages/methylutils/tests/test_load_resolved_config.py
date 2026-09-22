"""Tests for load_resolved_config helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_utils.action_config_resolver import load_resolved_config


def test_load_resolved_config_merges_step_override(tmp_path: Path) -> None:
    cfg_path = tmp_path / "resolved.json"
    cfg_path.write_text(json.dumps({"ppi_only": True, "top": 200}), encoding="utf-8")
    override_path = tmp_path / "override.json"
    override_path.write_text(json.dumps({"top": 50}), encoding="utf-8")

    merged = load_resolved_config(
        "enricher",
        resolved_config_path=cfg_path,
        step_override_path=override_path,
    )
    assert merged["ppi_only"] is True
    assert merged["top"] == 50


def test_load_resolved_config_requires_source() -> None:
    with pytest.raises(ValueError, match="requires resolved_config_path"):
        load_resolved_config("enricher")
