"""Unit tests for validation.model_mc handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import methyl_validation.model_mc_runner as model_mc_runner
from methyl_worker import handlers
from methyl_worker.depends import call_in_process_handler
from methyl_worker.task_models.validation_models import ModelMcTaskInput


def test_model_mc_handler_delegates_to_runner(tmp_path: Path) -> None:
    production = tmp_path / "production"
    production.mkdir()
    (production / "project.json").write_text("{}", encoding="utf-8")
    mc_root = tmp_path / "monte_carlo_runs"
    mc_root.mkdir()

    fake_result = {
        "status": "ok",
        "modelMcRoot": str(mc_root / "model_mc"),
        "backends": ["ecdf"],
        "nSharedIterations": 3,
    }

    task_input = ModelMcTaskInput.model_validate(
        {
            "projectPath": str(tmp_path / "project.json"),
            "monteCarloRunsRoot": str(mc_root),
        }
    )

    with patch.object(model_mc_runner, "run_model_mc_all", return_value=fake_result) as mock_run:
        with patch("methyl_worker.handlers.validation._load_mc_config") as mock_cfg:
            config = MagicMock()
            config.production_output_dir = None
            mock_cfg.return_value = (config, tmp_path / "project.json")
            out = call_in_process_handler(
                handlers._handle_validation_model_mc,
                "validation.model-mc",
                "validation.model_mc",
                task_input,
            )

    mock_run.assert_called_once()
    assert out.modelMcRoot.endswith("model_mc")
    assert out.n_iterations == 3


def test_load_mc_config_prefers_resolved_config_slice(tmp_path: Path) -> None:
    """When resolvedConfig is present, do not re-merge site/profile via resolve_for_project."""
    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")

    input_json = {
        "projectPath": str(project_json),
        "resolvedConfig": {
            "n_iterations": 5,
            "train_fraction": 0.8,
            "seed": 1,
            "dmp_modeling_mode": "raw_pool",
            "stability_featurecuts_enabled": False,
            "stability_min_balanced_accuracy": None,
            "stability_dmp_freq": 0.8,
        },
    }
    captured = {}

    def fake_load(base_project, request, **kwargs):
        captured["profile_overrides"] = kwargs.get("profile_overrides")
        return MagicMock(
            stability_featurecuts_enabled=False,
            stability_min_balanced_accuracy=None,
        )

    with patch(
        "methyl_validation.workflow_planner.resolve_base_project_json",
        return_value=project_json,
    ):
        with patch(
            "methyl_validation.workflow_planner._load_config_from_project",
            side_effect=fake_load,
        ):
            handlers._load_mc_config(input_json)

    assert captured["profile_overrides"] is not None
    assert captured["profile_overrides"].stability_featurecuts_enabled is False


def test_load_mc_config_ignores_non_planner_task_fields(tmp_path: Path) -> None:
    """Stability-shaped task input carries fields ValidationPlanRequest forbids.

    _load_mc_config must filter input_json down to planner fields (and normalize
    project -> projectPath) instead of validating the whole payload, otherwise
    pydantic raises extra_forbidden on tool/project/monteCarloRunsRoot/outputDir.
    """
    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")

    input_json = {
        "tool": "validation.stability",
        "project": str(project_json),
        "monteCarloRunsRoot": None,
        "outputDir": None,
        "featureIterations": None,
        "seed": None,
    }

    captured = {}

    def fake_load(base_project, request, **_kwargs):
        captured["projectPath"] = request.projectPath
        return MagicMock()

    with patch(
        "methyl_validation.workflow_planner.resolve_base_project_json",
        return_value=project_json,
    ):
        with patch(
            "methyl_validation.workflow_planner._load_config_from_project",
            side_effect=fake_load,
        ):
            config, base = handlers._load_mc_config(input_json)

    assert base == project_json
    assert captured["projectPath"] == str(project_json)


def test_load_mc_config_backfills_planner_fields_from_snapshot(tmp_path: Path) -> None:
    """Aggregation task input lacks train_fraction/n_iterations.

    _load_mc_config must backfill them from monte_carlo_runs/queue/mc_config.json so
    _load_config_from_project can build a valid MonteCarloConfig (they otherwise fail
    with Field required for train_fraction / n_iterations).
    """
    import json as _json

    project_json = tmp_path / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    mc_root = tmp_path / "monte_carlo_runs"
    (mc_root / "queue").mkdir(parents=True)
    (mc_root / "queue" / "mc_config.json").write_text(
        _json.dumps({"train_fraction": 0.8, "n_iterations": 10, "seed": 7}),
        encoding="utf-8",
    )

    captured = {}

    def fake_load(base_project, request, **_kwargs):
        captured["featureIterations"] = request.featureIterations
        captured["trainFraction"] = request.trainFraction
        captured["seed"] = request.seed
        return MagicMock()

    input_json = {
        "tool": "validation.stability",
        "projectPath": str(project_json),
        "monteCarloRunsRoot": str(mc_root),
        "outputDir": None,
    }

    with patch(
        "methyl_validation.workflow_planner.resolve_base_project_json",
        return_value=project_json,
    ):
        with patch(
            "methyl_validation.workflow_planner._load_config_from_project",
            side_effect=fake_load,
        ):
            handlers._load_mc_config(input_json)

    assert captured["featureIterations"] == 10
    assert captured["trainFraction"] == 0.8
    assert captured["seed"] == 7
