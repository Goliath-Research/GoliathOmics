"""Predictor resolves classifier slice separately from predictor resolvedConfig file."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from methyl_predictor.project_resolver import _resolve_classifier_step_for_predictor


def test_resolve_classifier_step_reads_nested_classifier_from_baked_file(tmp_path: Path) -> None:
    cfg_path = tmp_path / "predictor-resolved.json"
    cfg_path.write_text(
        json.dumps(
            {
                "debug": True,
                "classifier": {"save_classifier_path": "/work/models/ovr.pkl", "temperature": 0.5},
            }
        ),
        encoding="utf-8",
    )
    from methyl_utils.cli_resolved_config import resolve_cli_step_config

    project = MagicMock()
    step_cfg = resolve_cli_step_config(
        "predictor",
        project,
        resolved_config_path=cfg_path,
    )
    classifier_step = _resolve_classifier_step_for_predictor(
        project,
        step_cfg,
        resolved_config_path=cfg_path,
    )
    assert classifier_step["save_classifier_path"] == "/work/models/ovr.pkl"
    assert classifier_step["temperature"] == 0.5
    assert "debug" not in classifier_step


def test_model_path_falls_back_to_nested_classifier_save_path(tmp_path: Path) -> None:
    project = MagicMock()
    step_cfg = {"classifier": {"save_classifier_path": "/work/models/ovr.pkl"}}
    classifier_step = _resolve_classifier_step_for_predictor(
        project,
        step_cfg,
        resolved_config_path=tmp_path / "resolved.json",
    )
    model_path = step_cfg.get("model_path") or classifier_step.get("save_classifier_path")
    assert model_path == "/work/models/ovr.pkl"
