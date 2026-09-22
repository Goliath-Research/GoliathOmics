from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import numpy as np
import pandas as pd
from methyl_validation.model_datasets import read_dataset_frame
import pytest

from methyl_validation import generative_backend, model_bundle, pipeline_runner
from methyl_validation.config import MonteCarloConfig


class _StubProjectMulti:
    def __init__(self, detection_dir: Path):
        self.project_name = "stubproj"
        self._det = detection_dir

    def get_comparisons(self):
        return [
            SimpleNamespace(
                control_group="healthy",
                disease_group="pca1",
                comparison_label="healthy_vs_pca1",
            )
        ]

    def get_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        assert control_group == "healthy"
        assert disease_group == "pca1"
        return str(self._det)

    def resolve_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        return self.get_detection_output_dir(control_group, disease_group)

    def get_derived_paths(self):
        return SimpleNamespace(detection_dir=str(self._det))

    def get_resolved_groups(self):
        return [
            ("healthy", ["/tmp/S1", "/tmp/S2"]),
            ("pca1", ["/tmp/S3", "/tmp/S4"]),
            ("pca2", ["/tmp/S5", "/tmp/S6"]),
        ]


class _StubProjectBinary:
    def __init__(self, detection_dir: Path):
        self.project_name = "stubproj"
        self._det = detection_dir

    def get_comparisons(self):
        return [
            SimpleNamespace(
                control_group="healthy",
                disease_group="pca1",
                comparison_label="healthy_vs_pca1",
            )
        ]

    def get_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        assert control_group == "healthy"
        assert disease_group == "pca1"
        return str(self._det)

    def resolve_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        return self.get_detection_output_dir(control_group, disease_group)

    def get_derived_paths(self):
        return SimpleNamespace(detection_dir=str(self._det))

    def get_resolved_groups(self):
        return [
            ("healthy", ["/tmp/S1", "/tmp/S2"]),
            ("pca1", ["/tmp/S3", "/tmp/S4"]),
        ]


def _write_detector_dmps(det: Path) -> None:
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "1"],
            "position": [100, 120, 200],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.7, 0.4, 0.9],
            "weight": [0.8, 0.3, 1.0],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)


def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
    del chromosome, min_coverage
    positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
    n = len(sample_paths)
    X = np.zeros((n, len(positions)), dtype=np.float32)
    for i, p in enumerate(sample_paths):
        sid = Path(str(p)).name
        if sid in {"S1", "S2"}:
            base = 0.1
        elif sid in {"S3", "S4"}:
            base = 0.6
        else:
            base = 0.9
        X[i, :] = base
    ctx = np.asarray(["CG"] * len(positions), dtype=object)
    return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}


def test_generative_backend_multiclass_train_predict(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    _write_detector_dmps(det)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProjectMulti(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)

    monkeypatch.setattr(generative_backend, "load_project", lambda _p: _StubProjectMulti(det))
    monkeypatch.setattr(
        generative_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract,
    )
    model_dir = tmp_path / "model"
    generative_backend.train_generative_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        latent_dim=4,
    )
    assert (model_dir / "generative-model.npz").is_file()
    assert (model_dir / "generative-model-metadata.json").is_file()
    assert (model_dir / "training_metrics.json").is_file()
    with open(model_dir / "training_metrics.json", encoding="utf-8") as f:
        train_metrics = json.load(f)
    assert "balanced_accuracy" in train_metrics
    assert int(train_metrics.get("n_samples", 0)) == 6
    assert int(train_metrics.get("n_classes", 0)) == 3

    predictor_cfg = SimpleNamespace(
        test_group_paths=[
            {"label": "healthy", "class_index": 0, "paths": ["/tmp/S1", "/tmp/S2"]},
            {"label": "pca1", "class_index": 1, "paths": ["/tmp/S3", "/tmp/S4"]},
            {"label": "pca2", "class_index": 2, "paths": ["/tmp/S5", "/tmp/S6"]},
        ],
        test_control_paths=[],
        test_disease_paths=[],
    )
    monkeypatch.setattr(generative_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    metrics = generative_backend.predict_generative_model_from_project(
        project_json=tmp_path / "project.json",
        model_dir=model_dir,
        output_dir=tmp_path / "predict",
    )
    assert metrics["n_classes"] == 3
    assert "sensitivity" not in metrics
    assert "screening_binary" in metrics
    assert len(metrics.get("per_class") or []) == 3
    pred_df = pd.read_csv(tmp_path / "predict" / "predictions.csv")
    assert {"prob_class0", "prob_class1", "prob_class2"}.issubset(set(pred_df.columns))


def test_generative_backend_binary_predict_shape(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    _write_detector_dmps(det)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProjectBinary(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)

    monkeypatch.setattr(generative_backend, "load_project", lambda _p: _StubProjectBinary(det))
    monkeypatch.setattr(
        generative_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract,
    )
    model_dir = tmp_path / "model"
    generative_backend.train_generative_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        latent_dim=3,
    )
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(generative_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    metrics = generative_backend.predict_generative_model_from_project(
        project_json=tmp_path / "project.json",
        model_dir=model_dir,
        output_dir=tmp_path / "predict",
    )
    assert "balanced_accuracy" in metrics
    pred_df = pd.read_csv(tmp_path / "predict" / "predictions.csv")
    assert {"prob_class0", "prob_class1"}.issubset(set(pred_df.columns))


def test_generative_observed_hybrid_train_predict_schema_parity(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    _write_detector_dmps(det)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProjectBinary(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)

    monkeypatch.setattr(generative_backend, "load_project", lambda _p: _StubProjectBinary(det))
    monkeypatch.setattr(
        generative_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract,
    )
    model_dir = tmp_path / "model"
    generative_backend.train_generative_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        feature_mode="observed_hybrid",
        observed_feature_min_obs_fraction=0.75,
    )
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(generative_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    metrics = generative_backend.predict_generative_model_from_project(
        project_json=tmp_path / "project.json",
        model_dir=model_dir,
        output_dir=tmp_path / "predict",
    )
    assert "balanced_accuracy" in metrics
    pred_df = pd.read_csv(tmp_path / "predict" / "predictions.csv")
    assert {"obs_fraction", "low_evidence", "prediction_evidence_filtered"}.issubset(pred_df.columns)
    assert pred_df["low_evidence"].astype(bool).all()
    with open(model_dir / "generative-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert isinstance(meta.get("observed_healthy_reference_vector"), list)
    assert isinstance(meta.get("observed_cancer_reference_vector"), list)
    assert meta.get("observed_healthy_class_label") == "healthy"
    assert meta.get("observed_feature_order_fingerprint")


def test_resolve_eval_paths_multiclass_falls_back_from_binary_predictor(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    stub = _StubProjectMulti(det)
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(generative_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    monkeypatch.setattr(generative_backend, "load_project", lambda _p: stub)
    samples, y_true = generative_backend._resolve_eval_paths_and_labels(
        project_json=tmp_path / "project.json",
        class_names=["healthy", "pca1", "pca2"],
    )
    assert len(samples) == 6
    assert set(np.unique(y_true).tolist()) == {0, 1, 2}


def test_generative_resolve_eval_prefers_holdout_binary_paths(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    stub = _StubProjectBinary(det)
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        holdout_group_paths=None,
        train_group_paths=None,
        test_control_paths=[],
        test_disease_paths=[],
        train_control_paths=["/tmp/TR_C1", "/tmp/TR_C2"],
        train_disease_paths=["/tmp/TR_D1", "/tmp/TR_D2"],
        holdout_control_paths=["/tmp/HO_C1"],
        holdout_disease_paths=["/tmp/HO_D1", "/tmp/HO_D2"],
    )
    monkeypatch.setattr(generative_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    monkeypatch.setattr(generative_backend, "load_project", lambda _p: stub)
    samples, y_true = generative_backend._resolve_eval_paths_and_labels(
        project_json=tmp_path / "project.json",
        class_names=["healthy", "pca1"],
    )
    assert samples == ["/tmp/HO_C1", "/tmp/HO_D1", "/tmp/HO_D2"]
    assert y_true is not None
    assert y_true.tolist() == [0, 1, 1]


def test_generative_resolve_eval_prefers_holdout_group_paths_for_binary(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    stub = _StubProjectBinary(det)
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        holdout_group_paths=[
            {"label": "healthy", "class_index": 0, "paths": ["/tmp/HO_C1"]},
            {"label": "pca1", "class_index": 1, "paths": ["/tmp/HO_D1", "/tmp/HO_D2"]},
        ],
        train_group_paths=[
            {"label": "healthy", "class_index": 0, "paths": ["/tmp/TR_C1"]},
            {"label": "pca1", "class_index": 1, "paths": ["/tmp/TR_D1"]},
        ],
        test_control_paths=[],
        test_disease_paths=[],
        train_control_paths=[],
        train_disease_paths=[],
        holdout_control_paths=[],
        holdout_disease_paths=[],
    )
    monkeypatch.setattr(generative_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    monkeypatch.setattr(generative_backend, "load_project", lambda _p: stub)
    samples, y_true = generative_backend._resolve_eval_paths_and_labels(
        project_json=tmp_path / "project.json",
        class_names=["healthy", "pca1"],
    )
    assert samples == ["/tmp/HO_C1", "/tmp/HO_D1", "/tmp/HO_D2"]
    assert y_true is not None
    assert y_true.tolist() == [0, 1, 1]


def test_generative_covariates_strict_join(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    _write_detector_dmps(det)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProjectBinary(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)

    monkeypatch.setattr(generative_backend, "load_project", lambda _p: _StubProjectBinary(det))
    monkeypatch.setattr(
        generative_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract,
    )
    cov_csv = tmp_path / "cov.csv"
    pd.DataFrame(
        {
            "sample_id": ["S1", "S3"],
            "age": [50, 62],
        }
    ).to_csv(cov_csv, index=False)
    with pytest.raises(ValueError, match="Missing covariate rows"):
        generative_backend.train_generative_model(
            project_json=tmp_path / "project.json",
            bundle_h5=bundle_dir / "model_feature_bundle.h5",
            output_dir=tmp_path / "model",
            covariates_path=str(cov_csv),
            covariates_strict_join=True,
        )


def test_generative_covariate_preprocessor_written(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    _write_detector_dmps(det)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProjectBinary(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)

    monkeypatch.setattr(generative_backend, "load_project", lambda _p: _StubProjectBinary(det))
    monkeypatch.setattr(
        generative_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract,
    )
    cov_csv = tmp_path / "cov.csv"
    pd.DataFrame(
        {
            "sample_id": ["S1", "S2", "S3", "S4"],
            "age": [50, 52, 61, 64],
            "ethnicity": ["A", "A", "B", "C"],
        }
    ).to_csv(cov_csv, index=False)
    model_dir = tmp_path / "model"
    generative_backend.train_generative_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        covariates_path=str(cov_csv),
        covariates_strict_join=True,
    )
    assert (model_dir / "covariate-preprocessor.json").is_file()


def test_generative_composition_alr_uses_canonical_feature_names(
    tmp_path: Path, monkeypatch
) -> None:
    """Cell-fraction ALR names must match tabular/ECDF: alr_<part>_vs_<ref>."""
    det = tmp_path / "detections" / "healthy" / "pca1"
    _write_detector_dmps(det)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProjectBinary(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)

    monkeypatch.setattr(generative_backend, "load_project", lambda _p: _StubProjectBinary(det))
    monkeypatch.setattr(
        generative_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract,
    )
    columns = ["CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"]
    rows = []
    for i, sid in enumerate(["S1", "S2", "S3", "S4"]):
        cd8 = 0.05 + 0.01 * i
        cd4 = 0.15 + 0.01 * i
        nk = 0.04
        bcell = 0.03
        mono = 0.10
        neu = 1.0 - (cd8 + cd4 + nk + bcell + mono)
        rows.append(
            {
                "sample_id": sid,
                "CD8T": cd8,
                "CD4T": cd4,
                "NK": nk,
                "Bcell": bcell,
                "Mono": mono,
                "Neu": neu,
            }
        )
    cov_csv = tmp_path / "cell_fractions.csv"
    pd.DataFrame(rows).to_csv(cov_csv, index=False)
    model_dir = tmp_path / "model"
    generative_backend.train_generative_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        covariates_path=str(cov_csv),
        covariates_strict_join=True,
        covariate_composition_groups=[
            {
                "name": "cell_fractions",
                "columns": columns,
                "reference": "Neu",
                "pseudocount": 1e-6,
            }
        ],
    )
    prep = json.loads(
        (model_dir / "covariate-preprocessor.json").read_text(encoding="utf-8")
    )
    expected_alr = [
        "alr_CD8T_vs_Neu",
        "alr_CD4T_vs_Neu",
        "alr_NK_vs_Neu",
        "alr_Bcell_vs_Neu",
        "alr_Mono_vs_Neu",
    ]
    assert prep["output_columns"] == expected_alr
    assert all(not c.startswith("standardized_") for c in prep["output_columns"])
    meta = json.loads(
        (model_dir / "generative-model-metadata.json").read_text(encoding="utf-8")
    )
    assert meta["n_covariates"] == 5
    assert meta["selected_feature_count"] == meta["n_features"]
    selected = meta["selected_feature_names"]
    assert selected[-5:] == expected_alr
    assert len(selected) == meta["n_features"]
    report = meta["covariate_preprocessing"]
    assert report["composition_transform"] == "alr"
    assert report["composition_output_columns"] == expected_alr
    model_bundle_dir = tmp_path / "model_bundle"
    train_ds = read_dataset_frame(model_bundle_dir / "train_dataset.h5")
    assert list(train_ds.columns[:3]) == ["sample_id", "class_index", "class_label"]
    assert train_ds.columns.tolist()[-5:] == expected_alr
    assert (model_bundle_dir / "dataset_manifest.json").is_file()


def test_pipeline_runner_generative_backend_dispatch(tmp_path: Path, monkeypatch):
    def _fake_bundle(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / "model_feature_bundle.json"

    def _fake_train(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "generative-model.npz").write_bytes(b"test")
        (out_dir / "generative-model-metadata.json").write_text("{}", encoding="utf-8")
        return out_dir / "generative-model.npz"

    def _fake_predict(**kwargs):
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "validation_metrics.json").write_text("{}", encoding="utf-8")
        (out_dir / "predictions.csv").write_text("sample,prediction\nA,0\n", encoding="utf-8")
        return {"accuracy": 1.0}

    monkeypatch.setattr("methyl_validation.model_bundle.build_model_feature_bundle", _fake_bundle)
    monkeypatch.setattr("methyl_validation.generative_backend.train_generative_model", _fake_train)
    monkeypatch.setattr("methyl_validation.generative_backend.predict_generative_model_from_project", _fake_predict)

    # Analyte guard loads the project file; provide a minimal one on disk.
    (tmp_path / "project.json").write_text(
        json.dumps(
            {
                "project_name": "gen",
                "output_base": str(tmp_path),
                "samples_base_path": "/tmp",
                "controls": {"label": "healthy", "groups": [{"label": "healthy", "sample_paths": []}]},
                "diseases": {"label": "cancer", "groups": [{"label": "disease", "sample_paths": []}]},
            }
        ),
        encoding="utf-8",
    )

    config = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 2,
            "base_project": str(tmp_path / "project.json"),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {"enabled": False, "params": {}},
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": True, "params": {}},
            },
        }
    ).with_backend_selection("generative_hybrid")
    ok, errors, timings = pipeline_runner.run_pipeline_for_model(
        project_json=tmp_path / "project.json",
        predictor_output_dir=tmp_path / "predictors",
        config=config,
    )
    assert ok is True
    assert not errors
    assert [t["step_name"] for t in timings] == ["model-bundle", "generative-train", "generative-predictor"]


def test_generative_config_validation_strict_fields():
    with pytest.raises(ValueError, match="model_backend must be one of"):
        MonteCarloConfig.model_validate(
            {
                "samples_base_path": "/tmp",
                "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
                "train_fraction": 0.8,
                "n_iterations": 1,
                "base_project": "/tmp/project.json",
                "output_base": "/tmp",
                "model_backend": "unknown_backend",
            }
        )
    with pytest.raises(ValueError, match="generative_density_type must be one of"):
        MonteCarloConfig.model_validate(
            {
                "samples_base_path": "/tmp",
                "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
                "train_fraction": 0.8,
                "n_iterations": 1,
                "base_project": "/tmp/project.json",
                "output_base": "/tmp",
                "backend_profiles": {
                    "ecdf": {"enabled": False, "params": {}},
                    "tabular_sklearn": {"enabled": False, "params": {}},
                    "generative_hybrid": {
                        "enabled": True,
                        "params": {"generative_density_type": "full_covariance"},
                    },
                },
            }
        )

    with pytest.raises(ValueError, match="covariate role columns overlap"):
        MonteCarloConfig.model_validate(
            {
                "samples_base_path": "/tmp",
                "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
                "train_fraction": 0.8,
                "n_iterations": 1,
                "base_project": "/tmp/project.json",
                "output_base": "/tmp",
                "backend_profiles": {
                    "ecdf": {"enabled": False, "params": {}},
                    "tabular_sklearn": {
                        "enabled": True,
                        "params": {
                            "covariate_numeric_columns": ["age"],
                            "covariate_ordinal_columns": ["age"],
                        },
                    },
                    "generative_hybrid": {"enabled": False, "params": {}},
                },
            }
        )

    with pytest.raises(ValueError, match="covariate_ordinal_maps has columns not listed"):
        MonteCarloConfig.model_validate(
            {
                "samples_base_path": "/tmp",
                "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
                "train_fraction": 0.8,
                "n_iterations": 1,
                "base_project": "/tmp/project.json",
                "output_base": "/tmp",
                "backend_profiles": {
                    "ecdf": {"enabled": False, "params": {}},
                    "tabular_sklearn": {
                        "enabled": True,
                        "params": {
                            "covariate_ordinal_columns": ["severity"],
                            "covariate_ordinal_maps": {"risk_band": {"low": 1, "high": 2}},
                        },
                    },
                    "generative_hybrid": {"enabled": False, "params": {}},
                },
            }
        )


def test_generative_observed_hybrid_train_predict_schema_parity(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    _write_detector_dmps(det)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProjectBinary(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(generative_backend, "load_project", lambda _p: _StubProjectBinary(det))

    def _fake_extract_observed(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        if len(positions) >= 2:
            positions = positions[:2]
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(
        generative_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_observed,
    )
    model_dir = tmp_path / "model"
    generative_backend.train_generative_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        feature_mode="observed_hybrid",
        observed_feature_min_obs_fraction=0.75,
        latent_dim=3,
    )
    assert (model_dir / "training_metrics.json").is_file()
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(generative_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    metrics = generative_backend.predict_generative_model_from_project(
        project_json=tmp_path / "project.json",
        model_dir=model_dir,
        output_dir=tmp_path / "predict",
    )
    assert "balanced_accuracy" in metrics
    pred_df = pd.read_csv(tmp_path / "predict" / "predictions.csv")
    assert {"obs_fraction", "low_evidence", "prediction_evidence_filtered"}.issubset(pred_df.columns)
    assert pred_df["low_evidence"].astype(bool).all()
    with open(tmp_path / "predict" / "feature_family_ablation.json", encoding="utf-8") as f:
        ablation = json.load(f)
    assert ablation["backend"] == "generative_hybrid"
    assert "balanced_accuracy" in ablation
    with open(model_dir / "generative-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert isinstance(meta.get("observed_per_cancer_reference_vectors"), list)
    assert "max_weighted_directional_score" in set(meta.get("observed_feature_names") or [])
    assert "weighted_directional_agreement__pca1" in set(meta.get("observed_feature_names") or [])
    assert "weighted_cosine_similarity_to_cancer_centroid__pca1" in set(meta.get("observed_feature_names") or [])
    assert "weighted_healthy_tail_evidence__pca1" in set(meta.get("observed_feature_names") or [])
    assert "weighted_mean_abs_error_to_cancer_centroid__pca1" not in set(meta.get("observed_feature_names") or [])
    assert "weighted_fraction_dmps_closer_to_cancer_centroid__pca1" not in set(
        meta.get("observed_feature_names") or []
    )
    training_names = meta.get("training_feature_names") or []
    quality_names = meta.get("quality_feature_names") or []
    assert training_names
    assert quality_names
    assert {"obs_fraction", "n_obs_dmps", "n_total_dmps"}.issubset(set(quality_names))
    # selected_feature_names must match the training matrix (not full observed + quality).
    assert meta["selected_feature_count"] == meta["n_features"]
    assert meta["selected_feature_names"] == list(training_names)
    assert set(quality_names).isdisjoint(set(meta["selected_feature_names"]))
    assert float(meta.get("observed_hist_eps", 0.0)) > 0.0
    assert float(meta.get("observed_hist_alpha", -1.0)) >= 0.0
    assert float(meta.get("observed_hist_evidence_clip_cap", -1.0)) >= 0.0
    assert 0.0 <= float(meta.get("observed_hist_tail_agreement_threshold", -1.0)) <= 1.0

