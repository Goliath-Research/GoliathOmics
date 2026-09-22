"""Tests for workflow-facing model-MC handler options."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from methyl_worker.handlers.validation import (
    _handle_validation_model_mc,
    _handle_validation_model_train,
)
from methyl_worker.task_models.runtime_models import TaskRuntimeContext
from methyl_worker.task_models.validation_models import ModelMcTaskInput, ModelTrainTaskInput


def test_model_mc_handler_forwards_strict_artifact_reuse(tmp_path: Path) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    production_dir = mc_root / "production"
    config = SimpleNamespace(production_output_dir=str(production_dir))
    input_model = ModelMcTaskInput(
        projectPath=str(tmp_path / "project.json"),
        requireArtifactReuse=True,
    )

    with (
        patch(
            "methyl_worker.handlers.validation._load_mc_config",
            return_value=(config, tmp_path / "project.json"),
        ),
        patch(
            "methyl_validation.model_mc_runner.run_model_mc_all",
            return_value={
                "modelMcRoot": str(mc_root / "model_mc"),
                "nSharedIterations": 10,
            },
        ) as runner,
    ):
        output = _handle_validation_model_mc(
            "validation.model-mc",
            "validation.model_mc",
            input_model,
            runtime=TaskRuntimeContext(),
            mc_root=mc_root,
        )

    assert output.n_iterations == 10
    assert runner.call_args.kwargs["require_artifact_reuse"] is True


def test_model_train_does_not_treat_bundle_dir_as_h5(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    bundle_dir = tmp_path / "model_bundle"
    bundle_dir.mkdir()
    expected_h5 = tmp_path / "model_bundle" / "model_feature_bundle.h5"

    with (
        patch(
            "methyl_validation.tabular_backend.train_tabular_model",
            return_value={"model_path": str(tmp_path / "models" / "tabular-model.joblib")},
        ) as trainer,
    ):
        _handle_validation_model_train(
            "validation.model-train",
            "validation.model_train",
            ModelTrainTaskInput(
                projectPath=str(project),
                backend="tabular_sklearn",
                bundleDir=str(bundle_dir),
            ),
        )

    assert trainer.call_args.args[1] == expected_h5
