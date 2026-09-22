from __future__ import annotations

import json
from pathlib import Path

from methyl_validation.config import MonteCarloConfig
from methyl_validation.trainer_api import build_model_backend_steps


def _base_config(tmp_path: Path, enabled: bool) -> MonteCarloConfig:
    return MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "ecdf_second_stage_enabled": enabled,
                        "observed_feature_include_dmr": True,
                        "observed_feature_include_gene": True,
                        "observed_feature_max_dmrs": 7,
                        "observed_feature_max_genes": 9,
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )


def test_build_model_backend_steps_ecdf_includes_second_stage(tmp_path: Path, monkeypatch):
    called = {"n": 0, "kwargs": None}
    tm_called = {"n": 0, "args": None}

    def _fake_second_stage(**kwargs):
        called["n"] += 1
        called["kwargs"] = kwargs
        return {"ok": True, "predictor_output_dir": str(kwargs["predictor_output_dir"])}

    def _fake_training_metrics(project_json: Path, classifier_output_dir: Path):
        tm_called["n"] += 1
        tm_called["args"] = (project_json, classifier_output_dir)
        return True, str(classifier_output_dir / "training_metrics.json")

    monkeypatch.setattr("methyl_validation.ecdf_second_stage.train_and_apply_ecdf_second_stage", _fake_second_stage)
    monkeypatch.setattr("methyl_validation.trainer_api._write_ecdf_training_metrics", _fake_training_metrics)
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=_base_config(tmp_path, enabled=True),
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    assert [name for name, _fn in steps] == ["methyl-classifier", "methyl-predictor", "ecdf-second-stage"]
    rc, out, err = steps[-1][1]()
    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["training_metrics_saved"] is True
    assert called["n"] == 1
    assert tm_called["n"] == 1
    assert called["kwargs"] is not None
    assert tm_called["args"] is not None
    params = called["kwargs"]["params"]
    assert params.include_observed_hybrid is False
    assert params.max_dmr_features == 7
    assert params.max_gene_features == 9


def test_build_model_backend_steps_ecdf_second_stage_error_fails_step(tmp_path: Path, monkeypatch):
    def _boom(**_kwargs):
        raise RuntimeError("second-stage exploded")

    monkeypatch.setattr(
        "methyl_validation.ecdf_second_stage.train_and_apply_ecdf_second_stage",
        _boom,
    )
    monkeypatch.setattr(
        "methyl_validation.trainer_api._write_ecdf_training_metrics",
        lambda *_args, **_kwargs: (True, "training_metrics.json"),
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=_base_config(tmp_path, enabled=True),
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    rc, _out, err = steps[-1][1]()
    assert rc == 1
    assert "ECDF second-stage failed" in err
    assert "second-stage exploded" in err


def test_build_model_backend_steps_ecdf_hybrid_flag_is_explicit(tmp_path: Path, monkeypatch):
    called = {"params": None}

    def _fake_second_stage(**kwargs):
        called["params"] = kwargs.get("params")
        return {"ok": True}

    monkeypatch.setattr(
        "methyl_validation.ecdf_second_stage.train_and_apply_ecdf_second_stage",
        _fake_second_stage,
    )
    monkeypatch.setattr(
        "methyl_validation.trainer_api._write_ecdf_training_metrics",
        lambda *_a, **_k: (True, "tm"),
    )
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "ecdf_second_stage_enabled": True,
                        "ecdf_second_stage_include_observed_hybrid": True,
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    rc, _out, err = steps[-1][1]()
    assert rc == 0
    assert err == ""
    assert called["params"] is not None
    assert called["params"].include_observed_hybrid is True


def test_classic_ecdf_predictor_uses_partitioned_scoring_when_sidecars_exist(
    tmp_path: Path, monkeypatch
):
    project = tmp_path / "project.json"
    project.write_text(json.dumps({"samples_base_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "train_control.csv").write_text("sample\nC1\n", encoding="utf-8")
    (tmp_path / "train_disease.csv").write_text("sample\nD1\n", encoding="utf-8")
    (tmp_path / "test_control.csv").write_text("sample\nC2\n", encoding="utf-8")
    (tmp_path / "test_disease.csv").write_text("sample\nD2\n", encoding="utf-8")
    called = {"n": 0}

    def _fake_partitioned(project_json, predictor_output_dir):
        called["n"] += 1
        assert project_json == project
        assert predictor_output_dir == tmp_path / "predictors"
        return 0, '{"ok": true}', ""

    monkeypatch.setattr(
        "methyl_validation.trainer_api._run_classic_ecdf_partitioned_predictor",
        _fake_partitioned,
    )
    steps = build_model_backend_steps(
        project_json=project,
        predictor_output_dir=tmp_path / "predictors",
        config=_base_config(tmp_path, enabled=False),
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (_ for _ in ()).throw(AssertionError("fallback")),
    )
    rc, out, err = steps[1][1]()
    assert rc == 0
    assert err == ""
    assert called["n"] == 1
    assert json.loads(out)["ok"] is True


def test_score_classic_ecdf_test_partition_does_not_self_copy_test_metrics(
    tmp_path: Path, monkeypatch
):
    """test_metrics.json is the canonical path; do not shutil.copy2 it onto itself."""
    from methyl_validation.trainer_api import _score_classic_ecdf_partition

    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "predictors"
    out_dir.mkdir()
    model_dir = tmp_path / "detections" / "all" / "PCa"
    model_dir.mkdir(parents=True)
    (model_dir / "classifier-1-CG.pkl").write_bytes(b"x")

    def _fake_run_prediction(cfg):
        assert cfg.model_dir == str(model_dir)
        pred = Path(cfg.output_dir) / "predictions.csv"
        metrics = Path(cfg.output_dir) / "validation_metrics.json"
        pred.write_text("sample,expected_class,prediction\ns1,0,0\n", encoding="utf-8")
        metrics.write_text(json.dumps({"balanced_accuracy": 0.9}), encoding="utf-8")
        return {"balanced_accuracy": 0.9}

    class _FakePredictorConfig:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    base = _FakePredictorConfig(
        model_path=None,
        model_dir=str(model_dir),
        samples_base_path=str(tmp_path),
        path_remap=None,
        debug=False,
        classifier_step_snapshot=None,
        panel=None,
        decision_enabled=False,
        decision_min_margin=0.0,
        decision_min_confidence=0.0,
    )
    monkeypatch.setattr(
        "methyl_validation.trainer_api._resolve_classic_ecdf_model_locations",
        lambda _p: (base.model_path, base.model_dir, base),
    )
    monkeypatch.setattr("methyl_predictor.models.config.PredictorConfig", _FakePredictorConfig)
    monkeypatch.setattr("methyl_predictor.core.predictor.run_prediction", _fake_run_prediction)

    result = _score_classic_ecdf_partition(
        project_json=project,
        output_dir=out_dir,
        partition="test",
        control_paths=[str(tmp_path / "c1")],
        disease_paths=[str(tmp_path / "d1")],
    )
    assert (out_dir / "test_metrics.json").is_file()
    assert (out_dir / "validation_metrics.json").is_file()
    assert (out_dir / "test_predictions.csv").is_file()
    assert (out_dir / "predictions.csv").is_file()
    assert result["metrics_json"].endswith("test_metrics.json")
    assert json.loads((out_dir / "test_metrics.json").read_text(encoding="utf-8"))[
        "evaluation_partition"
    ] == "test"


def test_resolve_classic_ecdf_model_locations_uses_comparison_detection_dir(tmp_path: Path):
    from methyl_validation.trainer_api import _resolve_classic_ecdf_model_locations

    project = Path(
        "/work/projects/prostate-cancer/H_PCa_good_ecdf_covariates_extval10/"
        "monte_carlo_runs/model_mc/ecdf/run_0001/project.json"
    )
    if not project.is_file():
        import pytest

        pytest.skip("ECDF run_0001 project not available")
    model_path, model_dir, _base = _resolve_classic_ecdf_model_locations(project)
    # Prefer unified classifier pkl when present, else comparison-scoped detections.
    if model_path:
        assert model_path.endswith("run_0001-classifier.pkl")
        assert "classifiers/all/PCa" in model_path.replace("\\", "/")
    else:
        assert model_dir is not None
        assert model_dir.rstrip("/").endswith("detections/all/PCa")
        assert any(Path(model_dir).glob("classifier-*.pkl"))


def test_build_model_backend_steps_ecdf_second_stage_disabled(tmp_path: Path, monkeypatch):
    tm_called = {"n": 0}

    def _fake_training_metrics(project_json: Path, classifier_output_dir: Path):
        tm_called["n"] += 1
        return True, str(classifier_output_dir / "training_metrics.json")

    monkeypatch.setattr("methyl_validation.trainer_api._write_ecdf_training_metrics", _fake_training_metrics)
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=_base_config(tmp_path, enabled=False),
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    rc, out, err = steps[-1][1]()
    assert rc == 0
    assert "disabled" in out.lower()
    assert "training metrics" in out.lower()
    assert tm_called["n"] == 1
    assert err == ""


def test_build_model_backend_steps_ecdf_observed_hybrid_defaults_to_classic_path(tmp_path: Path):
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "feature_mode": "observed_hybrid",
                        "feature_family_set": "gene_scored",
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    assert [name for name, _ in steps] == ["methyl-classifier", "methyl-predictor", "ecdf-second-stage"]


def test_build_model_backend_steps_ecdf_raw_gene_uses_gene_ecdf_path(tmp_path: Path):
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "feature_mode": "raw_gene",
                        "feature_family_set": "gene_scored",
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    assert [name for name, _ in steps] == [
        "model-bundle",
        "ecdf-gene-train",
        "ecdf-gene-predictor",
        "ecdf-second-stage",
    ]


def test_raw_gene_predictor_emits_train_then_test_partitions(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [
                {"label": "healthy", "csv": "h.csv"},
                {"label": "disease", "csv": "d.csv"},
            ],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "feature_mode": "raw_gene",
                        "feature_family_set": "gene_scored",
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    partitions: list[str] = []

    def _fake_predict(**kwargs):
        partitions.append(kwargs["evaluation_partition"])
        return {"evaluation_partition": kwargs["evaluation_partition"]}

    monkeypatch.setattr(
        "methyl_validation.ecdf_gene_backend.predict_ecdf_gene_ovr_from_project",
        _fake_predict,
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "", ""),
        run_predictor_fn=lambda _p, _o: (0, "", ""),
    )

    rc, _out, err = steps[2][1]()

    assert rc == 0
    assert err == ""
    assert partitions == ["train", "test"]


def test_build_model_backend_steps_ecdf_observed_hybrid_uses_aggregated_path_when_explicit(tmp_path: Path):
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "feature_mode": "observed_hybrid",
                        "feature_family_set": "gene_scored",
                        "ecdf_aggregated_enabled": True,
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    assert [name for name, _ in steps] == [
        "model-bundle",
        "ecdf-aggregated-train",
        "ecdf-aggregated-predictor",
        "ecdf-second-stage",
    ]


def test_build_model_backend_steps_ecdf_aggregated_can_be_explicitly_disabled(tmp_path: Path):
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "feature_mode": "observed_hybrid",
                        "feature_family_set": "gene_scored",
                        "ecdf_aggregated_enabled": False,
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    assert [name for name, _ in steps] == ["methyl-classifier", "methyl-predictor", "ecdf-second-stage"]


def test_build_model_backend_steps_ecdf_aggregated_uses_configured_n_bins(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    def _fake_train(**kwargs):
        captured.update(kwargs)
        out = tmp_path / "classifiers" / "ecdf_aggregated_ovr.pkl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("x", encoding="utf-8")
        return out

    def _fake_predict(**kwargs):
        return {"ok": True}

    monkeypatch.setattr("methyl_validation.ecdf_aggregated_backend.train_ecdf_aggregated_ovr_model", _fake_train)
    monkeypatch.setattr("methyl_validation.ecdf_aggregated_backend.predict_ecdf_aggregated_ovr_from_project", _fake_predict)

    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "feature_mode": "observed_hybrid",
                        "feature_family_set": "gene_scored",
                        "ecdf_aggregated_enabled": True,
                        "ecdf_aggregated_n_bins": 211,
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "classifier ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "predictor ok", ""),
    )
    assert [name for name, _ in steps][:2] == ["model-bundle", "ecdf-aggregated-train"]
    rc, _out, _err = steps[1][1]()
    assert rc == 0
    assert captured.get("n_bins") == 211


def test_tabular_predictor_emits_train_then_test_partitions(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [
                {"label": "healthy", "csv": "h.csv"},
                {"label": "disease", "csv": "d.csv"},
            ],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {"enabled": False, "params": {}},
                "tabular_sklearn": {
                    "enabled": True,
                    "params": {"feature_mode": "observed_hybrid", "feature_family_set": "gene_scored"},
                },
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    partitions: list[str] = []

    def _fake_predict(**kwargs):
        partitions.append(kwargs["evaluation_partition"])
        return {"evaluation_partition": kwargs["evaluation_partition"]}

    monkeypatch.setattr(
        "methyl_validation.tabular_backend.predict_tabular_model_from_project",
        _fake_predict,
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "", ""),
        run_predictor_fn=lambda _p, _o: (0, "", ""),
    )
    names = [name for name, _ in steps]
    assert "tabular-predictor" in names
    rc, _out, err = steps[names.index("tabular-predictor")][1]()
    assert rc == 0
    assert err == ""
    assert partitions == ["train", "test"]


def test_generative_predictor_emits_train_then_test_partitions(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [
                {"label": "healthy", "csv": "h.csv"},
                {"label": "disease", "csv": "d.csv"},
            ],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {"enabled": False, "params": {}},
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {
                    "enabled": True,
                    "params": {"feature_mode": "observed_hybrid", "feature_family_set": "dmp_scored"},
                },
            },
        }
    )
    partitions: list[str] = []

    def _fake_predict(**kwargs):
        partitions.append(kwargs["evaluation_partition"])
        return {"evaluation_partition": kwargs["evaluation_partition"]}

    monkeypatch.setattr(
        "methyl_validation.generative_backend.predict_generative_model_from_project",
        _fake_predict,
    )
    steps = build_model_backend_steps(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=cfg,
        per_cancer_group=False,
        run_classifier_fn=lambda _p, _g: (0, "", ""),
        run_predictor_fn=lambda _p, _o: (0, "", ""),
    )
    names = [name for name, _ in steps]
    assert "generative-predictor" in names
    rc, _out, err = steps[names.index("generative-predictor")][1]()
    assert rc == 0
    assert err == ""
    assert partitions == ["train", "test"]
