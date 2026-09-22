"""Freeze readiness handler maps analyzer overall → ready."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import BaseModel, Field


class _Input(BaseModel):
    projectPath: str = Field(...)


def test_readiness_ready_true_for_go_with_risks(tmp_path: Path):
    from methyl_worker.handlers.validation import _handle_validation_stability_freeze_readiness

    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    fake_project = MagicMock()
    fake_project.output_base = str(tmp_path)
    fake_project.project_name = "study"

    report = {
        "verdict": {
            "overall": "go_with_risks",
            "reasons": ["soft warning"],
            "missing_artifacts": [],
        }
    }
    with (
        patch("methyl_utils.load_project", return_value=fake_project),
        patch(
            "methyl_validation.stability_freeze_readiness.analyze_project_root",
            return_value=report,
        ),
    ):
        out = _handle_validation_stability_freeze_readiness(
            "validation.stability-freeze-readiness",
            "validation.stability_freeze_readiness",
            _Input(projectPath=str(project_json)),
        )
    assert out.ready is True
    assert out.missing_artifacts == ["soft warning"]


def test_readiness_ready_false_for_no_go(tmp_path: Path):
    from methyl_worker.handlers.validation import _handle_validation_stability_freeze_readiness

    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    fake_project = MagicMock()
    fake_project.output_base = str(tmp_path)
    fake_project.project_name = "study"

    report = {"verdict": {"overall": "no_go", "reasons": ["Missing production_summary.json"]}}
    with (
        patch("methyl_utils.load_project", return_value=fake_project),
        patch(
            "methyl_validation.stability_freeze_readiness.analyze_project_root",
            return_value=report,
        ),
    ):
        out = _handle_validation_stability_freeze_readiness(
            "validation.stability-freeze-readiness",
            "validation.stability_freeze_readiness",
            _Input(projectPath=str(project_json)),
        )
    assert out.ready is False
    assert "Missing production_summary.json" in out.missing_artifacts
