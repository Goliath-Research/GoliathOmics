"""ECDF second-stage stacker with optional covariates."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from methyl_validation.model_datasets import read_dataset_frame
import pytest

from methyl_validation.config import MonteCarloConfig
from methyl_validation.ecdf_second_stage import (
    EcdfSecondStageParams,
    ecdf_second_stage_should_run,
    train_and_apply_ecdf_second_stage,
)
from methyl_validation.trainer_api import build_model_backend_steps


def _write_predictions(path: Path, n: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(n):
        sid = f"S{i}"
        y = 0 if i < n // 2 else 1
        # Separable-ish probs aligned with label
        p1 = 0.2 + 0.05 * i if y == 0 else 0.6 + 0.03 * i
        p1 = min(0.95, max(0.05, p1))
        rows.append(
            {
                "sample": sid,
                "sample_path": str(path.parent.parent / sid),
                "expected_class": y,
                "prob_class0": 1.0 - p1,
                "prob_class1": p1,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def _minimal_project(tmp_path: Path) -> Path:
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "ecdf_cov_test",
                "output_base": str(tmp_path),
                "samples_base_path": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    return project


def _write_covariates(path: Path, sample_ids: list[str], *, drop_one: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = sample_ids[:-1] if drop_one else sample_ids
    pd.DataFrame(
        {
            "sample_id": ids,
            "age": [40.0 + i for i in range(len(ids))],
            "bmi": [22.0 + 0.3 * i for i in range(len(ids))],
        }
    ).to_csv(path, index=False)


def _write_cell_composition(path: Path, sample_ids: list[str]) -> None:
    rows = []
    for index, sample_id in enumerate(sample_ids):
        cd8 = 0.0 if index % 3 == 0 else 0.05 + 0.002 * index
        cd4 = 0.15 + 0.002 * index
        nk = 0.04 + 0.001 * index
        bcell = 0.03 + 0.001 * index
        mono = 0.10 + 0.001 * index
        neu = 1.0 - (cd8 + cd4 + nk + bcell + mono)
        rows.append(
            {
                "sample_id": sample_id,
                "CD8T": cd8,
                "CD4T": cd4,
                "NK": nk,
                "Bcell": bcell,
                "Mono": mono,
                "Neu": neu,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_ecdf_second_stage_covariates_only(tmp_path: Path):
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    _write_predictions(pred_dir / "predictions.csv", n=8)
    sample_ids = [f"S{i}" for i in range(8)]
    cov_csv = tmp_path / "covariates.csv"
    _write_covariates(cov_csv, sample_ids)

    params = EcdfSecondStageParams(
        include_observed_hybrid=False,
        covariates_path=str(cov_csv),
        covariate_id_column="sample_id",
        covariate_numeric_columns=["age", "bmi"],
        covariates_strict_join=True,
        probability_epsilon=1e-6,
    )
    out = train_and_apply_ecdf_second_stage(
        project_json=project,
        predictor_output_dir=pred_dir,
        classifier_output_dir=clf_dir,
        params=params,
    )
    assert Path(out["model_path"]).is_file()
    assert (clf_dir / "covariate-preprocessor.json").is_file()
    meta = json.loads((clf_dir / "ecdf-second-stage-metadata.json").read_text(encoding="utf-8"))
    assert meta["include_covariates"] is True
    assert meta["include_observed_hybrid"] is False
    # ECDF class probs: binary -> closed non-reference probability.
    assert meta["n_prob_features"] == 1
    assert meta["prob_feature_names"] == ["prob_class1"]
    assert meta["n_covariate_features"] == 2
    assert meta["n_features"] == 3
    pred = pd.read_csv(pred_dir / "predictions.csv")
    assert "prob_refined_class0" in pred.columns
    assert "prediction_refined" in pred.columns


def test_ecdf_second_stage_fits_train_and_scores_disjoint_test(tmp_path: Path):
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    _write_predictions(pred_dir / "train_predictions.csv", n=8)
    _write_predictions(pred_dir / "test_predictions.csv", n=4)
    test_df = pd.read_csv(pred_dir / "test_predictions.csv")
    test_df["sample"] = [f"T{i}" for i in range(len(test_df))]
    test_df["sample_path"] = [str(tmp_path / f"T{i}") for i in range(len(test_df))]
    test_df.to_csv(pred_dir / "test_predictions.csv", index=False)
    cov_csv = tmp_path / "covariates.csv"
    _write_covariates(
        cov_csv,
        [f"S{i}" for i in range(8)] + [f"T{i}" for i in range(4)],
    )

    out = train_and_apply_ecdf_second_stage(
        project_json=project,
        predictor_output_dir=pred_dir,
        classifier_output_dir=clf_dir,
        params=EcdfSecondStageParams(
            include_observed_hybrid=False,
            covariates_path=str(cov_csv),
            covariate_id_column="sample_id",
            covariate_numeric_columns=["age", "bmi"],
            covariates_strict_join=True,
            probability_epsilon=1e-6,
        ),
    )

    assert out["test_predictions_csv"] == str(pred_dir / "test_predictions.csv")
    assert len(pd.read_csv(pred_dir / "train_predictions.csv")) == 8
    assert len(pd.read_csv(pred_dir / "test_predictions.csv")) == 4
    test_metrics = json.loads(
        (pred_dir / "test_metrics.json").read_text(encoding="utf-8")
    )
    assert test_metrics["evaluation_partition"] == "test"
    assert test_metrics["n_train_samples"] == 8
    assert test_metrics["n_test_samples"] == 4
    assert test_metrics["train_test_overlap_count"] == 0
    assert (pred_dir / "validation_metrics.json").read_text(encoding="utf-8") == (
        pred_dir / "test_metrics.json"
    ).read_text(encoding="utf-8")
    dataset_dir = tmp_path / "model_bundle"
    train_dataset = read_dataset_frame(dataset_dir / "train_dataset.h5")
    test_dataset = read_dataset_frame(dataset_dir / "test_dataset.h5")
    expected_columns = [
        "sample_id",
        "class_index",
        "class_label",
        "prob_class1",
        "standardized_age",
        "standardized_bmi",
    ]
    assert train_dataset.columns.tolist() == expected_columns
    assert test_dataset.columns.tolist() == expected_columns
    assert "evaluation_partition" not in train_dataset.columns
    assert "evaluation_partition" not in test_dataset.columns
    preprocessor = json.loads(
        (clf_dir / "covariate-preprocessor.json").read_text(encoding="utf-8")
    )
    expected_train_age = (
        40.0 - preprocessor["numeric_means"]["age"]
    ) / preprocessor["numeric_stds"]["age"]
    expected_test_age = (
        48.0 - preprocessor["numeric_means"]["age"]
    ) / preprocessor["numeric_stds"]["age"]
    assert train_dataset.loc[0, "standardized_age"] == pytest.approx(
        expected_train_age
    )
    assert test_dataset.loc[0, "standardized_age"] == pytest.approx(
        expected_test_age
    )
    manifest = json.loads(
        (dataset_dir / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["n_train_samples"] == 8
    assert manifest["n_test_samples"] == 4
    assert manifest["train_test_overlap_count"] == 0
    assert manifest["overlapping_sample_ids"] == []
    assert out["dataset_manifest_json"] == str(
        dataset_dir / "dataset_manifest.json"
    )


def test_ecdf_second_stage_rejects_overlapping_export_datasets(tmp_path: Path):
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    _write_predictions(pred_dir / "train_predictions.csv", n=8)
    _write_predictions(pred_dir / "test_predictions.csv", n=4)
    cov_csv = tmp_path / "covariates.csv"
    _write_covariates(cov_csv, [f"S{i}" for i in range(8)])

    with pytest.raises(ValueError, match="train/test datasets overlap"):
        train_and_apply_ecdf_second_stage(
            project_json=project,
            predictor_output_dir=pred_dir,
            classifier_output_dir=clf_dir,
            params=EcdfSecondStageParams(
                include_observed_hybrid=False,
                covariates_path=str(cov_csv),
                covariate_id_column="sample_id",
                covariate_numeric_columns=["age", "bmi"],
                covariates_strict_join=True,
                probability_epsilon=1e-6,
            ),
        )


def test_ecdf_second_stage_uses_one_logit_and_five_alr_features(
    tmp_path: Path,
) -> None:
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    _write_predictions(pred_dir / "train_predictions.csv", n=8)
    _write_predictions(pred_dir / "test_predictions.csv", n=4)
    test_df = pd.read_csv(pred_dir / "test_predictions.csv")
    test_df["sample"] = [f"T{i}" for i in range(4)]
    test_df["sample_path"] = [str(tmp_path / f"T{i}") for i in range(4)]
    test_df.to_csv(pred_dir / "test_predictions.csv", index=False)
    cov_csv = tmp_path / "cell_fractions.csv"
    columns = ["CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"]
    _write_cell_composition(
        cov_csv,
        [f"S{i}" for i in range(8)] + [f"T{i}" for i in range(4)],
    )

    train_and_apply_ecdf_second_stage(
        project_json=project,
        predictor_output_dir=pred_dir,
        classifier_output_dir=clf_dir,
        params=EcdfSecondStageParams(
            include_observed_hybrid=False,
            covariates_path=str(cov_csv),
            covariates_strict_join=True,
            probability_epsilon=1e-6,
            composition_groups=[
                {
                    "name": "cell_fractions",
                    "columns": columns,
                    "reference": "Neu",
                    "pseudocount": 1e-6,
                }
            ],
        ),
    )

    dataset_dir = tmp_path / "model_bundle"
    train_dataset = read_dataset_frame(dataset_dir / "train_dataset.h5")
    test_dataset = read_dataset_frame(dataset_dir / "test_dataset.h5")
    feature_columns = [
        "prob_class1",
        "alr_CD8T_vs_Neu",
        "alr_CD4T_vs_Neu",
        "alr_NK_vs_Neu",
        "alr_Bcell_vs_Neu",
        "alr_Mono_vs_Neu",
    ]
    assert train_dataset.columns.tolist() == [
        "sample_id",
        "class_index",
        "class_label",
        *feature_columns,
    ]
    assert test_dataset.columns.tolist() == [
        "sample_id",
        "class_index",
        "class_label",
        *feature_columns,
    ]
    assert np.isfinite(train_dataset[feature_columns].to_numpy()).all()
    assert np.isfinite(test_dataset[feature_columns].to_numpy()).all()
    # Binary simplex: closed non-reference probability (class1), not ALR.
    assert train_dataset.loc[0, "prob_class1"] == pytest.approx(0.2, abs=1e-4)
    assert train_dataset["prob_class1"].dtype == np.float32
    manifest = json.loads(
        (dataset_dir / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["feature_columns"] == feature_columns
    assert manifest["probability_transform"] == "probability"
    assert manifest["composition_groups"][0]["name"] == "cell_fractions"
    assert manifest["composition_groups"][0]["reference"] == "Neu"
    assert manifest["train_test_overlap_count"] == 0
    preprocessor = json.loads(
        (clf_dir / "covariate-preprocessor.json").read_text(encoding="utf-8")
    )
    assert preprocessor["composition_groups"][0]["columns"] == columns
    assert preprocessor["composition_groups"][0]["reference"] == "Neu"
    assert len(preprocessor["output_columns"]) == 5


def test_ecdf_second_stage_strict_join_missing_ids(tmp_path: Path):
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    _write_predictions(pred_dir / "predictions.csv", n=6)
    cov_csv = tmp_path / "covariates.csv"
    _write_covariates(cov_csv, [f"S{i}" for i in range(6)], drop_one=True)

    params = EcdfSecondStageParams(
        include_observed_hybrid=False,
        covariates_path=str(cov_csv),
        covariates_strict_join=True,
        covariate_numeric_columns=["age", "bmi"],
        probability_epsilon=1e-6,
    )
    with pytest.raises(ValueError, match="Missing covariate rows"):
        train_and_apply_ecdf_second_stage(
            project_json=project,
            predictor_output_dir=pred_dir,
            classifier_output_dir=clf_dir,
            params=params,
        )


def test_ecdf_second_stage_drop_missing_ids_continues(tmp_path: Path, capsys):
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    _write_predictions(pred_dir / "predictions.csv", n=8)
    cov_csv = tmp_path / "covariates.csv"
    _write_covariates(cov_csv, [f"S{i}" for i in range(8)], drop_one=True)

    params = EcdfSecondStageParams(
        include_observed_hybrid=False,
        covariates_path=str(cov_csv),
        covariates_strict_join=True,
        covariates_missing_samples="drop",
        covariate_numeric_columns=["age", "bmi"],
        probability_epsilon=1e-6,
    )
    out = train_and_apply_ecdf_second_stage(
        project_json=project,
        predictor_output_dir=pred_dir,
        classifier_output_dir=clf_dir,
        params=params,
    )
    assert Path(out["model_path"]).is_file()
    captured = capsys.readouterr()
    assert "Dropping 1 sample" in captured.err
    train_metrics = json.loads((pred_dir / "train_metrics.json").read_text(encoding="utf-8"))
    assert train_metrics["n_samples_excluded_missing_covariates"] == 1
    assert train_metrics["dropped_sample_ids"] == ["S7"]
    assert train_metrics["n_train_samples"] == 7
    meta = json.loads((clf_dir / "ecdf-second-stage-metadata.json").read_text(encoding="utf-8"))
    assert meta["covariate_report"]["n_dropped"] == 1
    assert meta["covariate_report"]["dropped_sample_ids"] == ["S7"]


def test_ecdf_second_stage_requires_stack_components():
    params = EcdfSecondStageParams(include_observed_hybrid=False, covariates_path=None)
    with pytest.raises(ValueError, match="include_observed_hybrid and/or covariates_path"):
        params.validate_stack_components()


def test_ecdf_second_stage_binary_allows_missing_probability_epsilon(tmp_path: Path):
    """Binary class probs use raw p; epsilon is not required."""
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    _write_predictions(pred_dir / "predictions.csv", n=8)
    cov_csv = tmp_path / "covariates.csv"
    _write_covariates(cov_csv, [f"S{i}" for i in range(8)])
    out = train_and_apply_ecdf_second_stage(
        project_json=project,
        predictor_output_dir=pred_dir,
        classifier_output_dir=clf_dir,
        params=EcdfSecondStageParams(
            include_observed_hybrid=False,
            covariates_path=str(cov_csv),
            covariate_numeric_columns=["age", "bmi"],
            covariates_strict_join=True,
        ),
    )
    assert Path(out["model_path"]).is_file()


def test_ecdf_second_stage_multiclass_requires_probability_epsilon(tmp_path: Path):
    project = _minimal_project(tmp_path)
    pred_dir = tmp_path / "predictors"
    clf_dir = tmp_path / "classifiers"
    path = pred_dir / "predictions.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(9):
        y = i % 3
        probs = [0.1, 0.1, 0.1]
        probs[y] = 0.8
        rows.append(
            {
                "sample": f"S{i}",
                "sample_path": str(tmp_path / f"S{i}"),
                "expected_class": y,
                "prob_class0": probs[0],
                "prob_class1": probs[1],
                "prob_class2": probs[2],
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    with pytest.raises(ValueError, match="ecdf_second_stage_probability_epsilon is required"):
        train_and_apply_ecdf_second_stage(
            project_json=project,
            predictor_output_dir=pred_dir,
            classifier_output_dir=clf_dir,
            params=EcdfSecondStageParams(
                include_observed_hybrid=True,
                covariates_path=None,
            ),
        )


def test_ecdf_second_stage_should_run_gate():
    assert ecdf_second_stage_should_run(None) is False
    cfg_off = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": "/tmp/p.json",
            "output_base": "/tmp",
            "backend_profiles": {
                "ecdf": {"enabled": True, "params": {"ecdf_second_stage_enabled": False}},
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    assert ecdf_second_stage_should_run(cfg_off) is False
    cfg_enabled = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": "/tmp/p.json",
            "output_base": "/tmp",
            "backend_profiles": {
                "ecdf": {"enabled": True, "params": {"ecdf_second_stage_enabled": True}},
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    assert ecdf_second_stage_should_run(cfg_enabled) is True
    params_enabled = EcdfSecondStageParams.from_monte_carlo_config(cfg_enabled)
    assert params_enabled.include_observed_hybrid is False
    cfg_cov = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": "/tmp/p.json",
            "output_base": "/tmp",
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        # Contradictory false+covariates is coerced to enabled=true.
                        "ecdf_second_stage_enabled": False,
                        "covariates_path": "/tmp/cov.csv",
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    assert cfg_cov.ecdf_second_stage_enabled is True
    assert ecdf_second_stage_should_run(cfg_cov) is True
    cfg_hybrid_only = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": "/tmp/p.json",
            "output_base": "/tmp",
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "ecdf_second_stage_enabled": False,
                        "ecdf_second_stage_include_observed_hybrid": True,
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    assert ecdf_second_stage_should_run(cfg_hybrid_only) is False


def test_filter_dmp_df_to_frozen_genes_keeps_panel_only():
    from methyl_validation.ecdf_second_stage import _filter_dmp_df_to_frozen_genes

    dmp_df = pd.DataFrame(
        {
            "chromosome": ["1", "1", "2"],
            "position": [10, 20, 30],
            "gene_name": ["TP53", "BRCA1", "UNSTABLE1"],
            "effect_size": [0.2, 0.1, 0.9],
        }
    )
    panel = pd.DataFrame(
        {"gene_name": ["TP53", "BRCA1"], "gene_support_n": [1, 1], "gene_importance": [1.0, 0.5]}
    )
    filtered = _filter_dmp_df_to_frozen_genes(dmp_df, panel)
    assert set(filtered["gene_name"]) == {"TP53", "BRCA1"}


def test_from_monte_carlo_config_hybrid_flag_is_decoupled():
    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/tmp",
            "cohorts": [{"label": "healthy", "csv": "h.csv"}, {"label": "disease", "csv": "d.csv"}],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": "/tmp/p.json",
            "output_base": "/tmp",
            "backend_profiles": {
                "ecdf": {
                    "enabled": True,
                    "params": {
                        "ecdf_second_stage_enabled": True,
                        "ecdf_second_stage_include_observed_hybrid": True,
                        "covariates_path": "/tmp/cov.csv",
                    },
                },
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
        }
    )
    params = EcdfSecondStageParams.from_monte_carlo_config(cfg)
    assert params.include_observed_hybrid is True
    assert params.has_covariates() is True


def test_raw_gene_second_stage_runs_when_covariates_path_set(tmp_path: Path, monkeypatch):
    called = {"n": 0, "params": None}

    def _fake_second_stage(**kwargs):
        called["n"] += 1
        called["params"] = kwargs.get("params")
        return {"ok": True}

    monkeypatch.setattr(
        "methyl_validation.ecdf_second_stage.train_and_apply_ecdf_second_stage",
        _fake_second_stage,
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
                        "feature_mode": "raw_gene",
                        "feature_family_set": "gene_scored",
                        "covariates_path": str(tmp_path / "cov.csv"),
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
        run_classifier_fn=lambda _p, _g: (0, "ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "ok", ""),
    )
    assert [name for name, _ in steps][-1] == "ecdf-second-stage"
    rc, out, err = steps[-1][1]()
    assert rc == 0
    assert err == ""
    assert called["n"] == 1
    assert isinstance(called["params"], EcdfSecondStageParams)
    assert called["params"].has_covariates() is True
    assert called["params"].include_observed_hybrid is False
    payload = json.loads(out)
    assert payload["ok"] is True


def test_raw_gene_second_stage_skips_when_neither_gate(tmp_path: Path, monkeypatch):
    called = {"n": 0}

    def _fake_second_stage(**kwargs):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(
        "methyl_validation.ecdf_second_stage.train_and_apply_ecdf_second_stage",
        _fake_second_stage,
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
        run_classifier_fn=lambda _p, _g: (0, "ok", ""),
        run_predictor_fn=lambda _p, _o: (0, "ok", ""),
    )
    rc, out, err = steps[-1][1]()
    assert rc == 0
    assert called["n"] == 0
    assert "skipped" in out.lower()
    assert err == ""
