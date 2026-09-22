"""Tests for the one-shot post-model validation output layout."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from methyl_worker.handlers.validation import _handle_validation_post_model_validation
from methyl_worker.task_models.runtime_models import TaskRuntimeContext
from methyl_worker.task_models.validation_models import PostModelValidationTaskInput


def _config(production_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        cohorts=["control", "disease"],
        production_output_dir=str(production_dir),
    )


@pytest.mark.parametrize(
    ("path_field", "relative_output"),
    [
        (None, "post_model_validation"),
        ("outputDir", "custom-output"),
        ("runDir", "legacy-run"),
    ],
)
def test_post_model_validation_binary_output_location(
    tmp_path: Path,
    path_field: str | None,
    relative_output: str,
) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    production_dir = mc_root / "production"
    production_dir.mkdir(parents=True)
    (production_dir / "project.json").write_text("{}", encoding="utf-8")
    (mc_root / "val_control.csv").write_text("sample\nC1\n", encoding="utf-8")
    (mc_root / "val_disease.csv").write_text("sample\nD1\n", encoding="utf-8")

    payload: dict[str, str] = {"projectPath": str(tmp_path / "project.json")}
    expected = mc_root / relative_output
    if path_field is not None:
        payload[path_field] = str(expected)
    input_model = PostModelValidationTaskInput.model_validate(payload)

    with (
        patch(
            "methyl_worker.handlers.validation._load_mc_config",
            return_value=(_config(production_dir), tmp_path / "project.json"),
        ),
        patch("methyl_validation.project_gen.infer_monte_carlo_layout", return_value="binary"),
        patch(
            "methyl_validation.pipeline_runner.run_post_model_validation_binary",
            return_value=(True, [], []),
        ) as runner,
    ):
        output = _handle_validation_post_model_validation(
            "validation.post-model-validation",
            "validation.post_model_validation",
            input_model,
            runtime=TaskRuntimeContext(),
            mc_root=mc_root,
        )

    assert output.outputDir == str(expected)
    assert output.report_path == str(expected / "post_model_validation_report.json")
    assert Path(output.report_path).is_file()
    assert runner.call_args.kwargs["predictor_output_dir"] == expected / "predictors"
    assert runner.call_args.kwargs["logs_dir"] == expected / "logs"


def test_post_model_validation_multiclass_uses_root_default(tmp_path: Path) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    production_dir = mc_root / "production"
    production_dir.mkdir(parents=True)
    (production_dir / "project.json").write_text("{}", encoding="utf-8")
    test_groups = mc_root / "test_groups.json"
    test_groups.write_text("{}", encoding="utf-8")
    expected = mc_root / "post_model_validation"

    with (
        patch(
            "methyl_worker.handlers.validation._load_mc_config",
            return_value=(_config(production_dir), tmp_path / "project.json"),
        ),
        patch("methyl_validation.project_gen.infer_monte_carlo_layout", return_value="multiclass"),
        patch(
            "methyl_validation.pipeline_runner.run_post_model_validation_multiclass",
            return_value=(True, [], []),
        ) as runner,
    ):
        output = _handle_validation_post_model_validation(
            "validation.post-model-validation",
            "validation.post_model_validation",
            PostModelValidationTaskInput(projectPath=str(tmp_path / "project.json")),
            runtime=TaskRuntimeContext(),
            mc_root=mc_root,
        )

    assert output.outputDir == str(expected)
    assert runner.call_args.kwargs["test_groups_json"] == test_groups
    assert runner.call_args.kwargs["predictor_output_dir"] == expected / "predictors"
    assert runner.call_args.kwargs["logs_dir"] == expected / "logs"


def test_post_model_validation_multiclass_accepts_val_groups_alias(tmp_path: Path) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    production_dir = mc_root / "production"
    production_dir.mkdir(parents=True)
    (production_dir / "project.json").write_text("{}", encoding="utf-8")
    alias_groups = tmp_path / "legacy_val_groups.json"
    alias_groups.write_text("{}", encoding="utf-8")
    expected = mc_root / "post_model_validation"

    with (
        patch(
            "methyl_worker.handlers.validation._load_mc_config",
            return_value=(_config(production_dir), tmp_path / "project.json"),
        ),
        patch("methyl_validation.project_gen.infer_monte_carlo_layout", return_value="multiclass"),
        patch(
            "methyl_validation.pipeline_runner.run_post_model_validation_multiclass",
            return_value=(True, [], []),
        ) as runner,
    ):
        output = _handle_validation_post_model_validation(
            "validation.post-model-validation",
            "validation.post_model_validation",
            PostModelValidationTaskInput(
                projectPath=str(tmp_path / "project.json"),
                valGroupsJson=str(alias_groups),
            ),
            runtime=TaskRuntimeContext(),
            mc_root=mc_root,
        )

    assert output.outputDir == str(expected)
    assert runner.call_args.kwargs["test_groups_json"] == alias_groups
