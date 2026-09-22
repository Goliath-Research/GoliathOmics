from __future__ import annotations

from pathlib import Path

from methyl_validation.config import MonteCarloConfig
from methyl_validation import generative_backend, tabular_backend
from methyl_validation.pipeline_runner import (
    run_post_model_validation_binary,
    run_post_model_validation_multiclass,
)


def _mc_config(tmp_path: Path, backend: str) -> MonteCarloConfig:
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp/samples",
            "cohorts": [{"label": "healthy", "csv": "healthy.csv"}, {"label": "disease", "csv": "disease.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 2,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {"enabled": True, "params": {}},
                "tabular_sklearn": {"enabled": True, "params": {}},
                "generative_hybrid": {"enabled": True, "params": {}},
            },
        }
    )
    return cfg.with_backend_selection(backend)


def test_post_model_runner_binary_ecdf(monkeypatch, tmp_path: Path):
    calls: list[tuple] = []

    def _fake_predict(project_json, val_control_csv, val_disease_csv, predictor_output_dir):
        calls.append((project_json, val_control_csv, val_disease_csv, predictor_output_dir))
        return 0, "ok", ""

    monkeypatch.setattr("methyl_validation.pipeline_runner.run_predictor", _fake_predict)
    ok, errors, timings = run_post_model_validation_binary(
        project_json=tmp_path / "run_project.json",
        val_control_csv=tmp_path / "val_control.csv",
        val_disease_csv=tmp_path / "val_disease.csv",
        predictor_output_dir=tmp_path / "run_0001" / "predictors",
        production_output_dir=tmp_path / "production",
        config=_mc_config(tmp_path, "ecdf"),
    )
    assert ok is True
    assert errors == []
    assert [t["step_name"] for t in timings] == ["methyl-predictor"]
    assert len(calls) == 1


def test_post_model_runner_binary_tabular(monkeypatch, tmp_path: Path):
    def _fake_predict(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "validation_metrics.json").write_text('{"balanced_accuracy": 0.9}', encoding="utf-8")
        return {"balanced_accuracy": 0.9}

    monkeypatch.setattr(tabular_backend, "predict_tabular_model_from_project", _fake_predict)
    ok, errors, timings = run_post_model_validation_binary(
        project_json=tmp_path / "run_project.json",
        val_control_csv=tmp_path / "val_control.csv",
        val_disease_csv=tmp_path / "val_disease.csv",
        predictor_output_dir=tmp_path / "run_0001" / "predictors",
        production_output_dir=tmp_path / "production",
        config=_mc_config(tmp_path, "tabular_sklearn"),
    )
    assert ok is True
    assert errors == []
    assert [t["step_name"] for t in timings] == ["tabular-predictor"]


def test_post_model_runner_multiclass_generative(monkeypatch, tmp_path: Path):
    def _fake_predict(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "validation_metrics.json").write_text('{"balanced_accuracy": 0.83}', encoding="utf-8")
        return {"balanced_accuracy": 0.83}

    monkeypatch.setattr(generative_backend, "predict_generative_model_from_project", _fake_predict)
    ok, errors, timings = run_post_model_validation_multiclass(
        project_json=tmp_path / "run_project.json",
        test_groups_json=tmp_path / "val_groups.json",
        predictor_output_dir=tmp_path / "run_0001" / "predictors",
        production_output_dir=tmp_path / "production",
        config=_mc_config(tmp_path, "generative_hybrid"),
    )
    assert ok is True
    assert errors == []
    assert [t["step_name"] for t in timings] == ["generative-predictor"]
