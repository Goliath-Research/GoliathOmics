"""Regression tests for predictor metrics, path normalization, and prediction_report.json."""

import csv
import importlib
import json
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_predictor.models.config import PredictorConfig
from methyl_predictor.core.predictor import _expand_nested_labeled_paths, run_prediction
from methyl_utils.ecdf_aggregated_ovr import train_aggregated_ecdf_ovr_package

pytestmark = pytest.mark.filterwarnings(
    "ignore:Labeled metrics are undifferentiated:UserWarning",
)


class _DummyClassifier:
    def __init__(self, *_args, **_kwargs):
        self.n_classes = 2
        self.class_names = ["control", "disease"]
        self.is_multi_chromosome = False
        self.classifier = self

    def get_feature_info(self):
        return {"positions": [10, 20]}


class _DummyOvRClassifier:
    """Minimal stand-in for OvR-loaded MethylClassifier (no real ECDF)."""

    def __init__(self, *_args, **_kwargs):
        self.n_classes = 3
        self.class_names = ["g0", "g1", "g2"]
        self.is_multi_chromosome = False
        self.classifier = None
        self._ovr_mode = True
        self._ovr_binary_classifiers = [object(), object(), object()]
        self.dmp_positions_df = pd.DataFrame(
            {"chromosome": ["1", "1"], "position": [10, 20]}
        )

    def get_feature_info(self):
        return {"positions": np.array([10, 20], dtype=np.uint32), "n_features": 2}


class _DummyAggregatedOvRClassifier:
    def __init__(self, *_args, **_kwargs):
        self.n_classes = 2
        self.class_names = ["healthy", "disease"]
        self.is_multi_chromosome = False
        self.classifier = None
        self._ovr_mode = False
        self._aggregated_ovr_mode = True
        self.metadata = {"classifier_type": "ecdf_aggregated_one_vs_rest"}
        X = np.asarray(
            [
                [0.1, 0.2, 0.1],
                [0.2, 0.2, 0.2],
                [0.8, 0.9, 0.8],
                [0.9, 0.9, 0.9],
            ],
            dtype=np.float64,
        )
        y = np.asarray([0, 0, 1, 1], dtype=np.int32)
        pkg = train_aggregated_ecdf_ovr_package(
            X,
            y,
            class_names=["healthy", "disease"],
            feature_names=["gene_obs_fraction", "gene_weighted_shift_vs_healthy", "struct_promoter_obs_fraction"],
            feature_weights=np.asarray([1.0, 1.0, 1.0], dtype=np.float64),
            feature_family_set="gene_scored",
            feature_mode="observed_hybrid",
            n_bins=16,
            temperature=1.0,
        )
        pkg["observed_hybrid"] = {
            "dmp_df": pd.DataFrame({"effect_size": [0.2], "gene_name": ["A"], "feature_type": ["promoter"]}),
            "healthy_reference_vector": np.asarray([0.5], dtype=np.float64),
            "cancer_reference_vector": np.asarray([0.6], dtype=np.float64),
            "per_cancer_reference_vectors": [np.asarray([0.6], dtype=np.float64)],
            "healthy_class_label": "healthy",
            "cancer_class_labels": ["disease"],
            "anchor_strategy": "class_centroid",
            "feature_order_fingerprint": "",
            "feature_family_set": "gene_scored",
            "class_centroid_dirs": {},
            "min_coverage": 1,
            "hist_eps": 1e-6,
            "hist_alpha": 0.5,
            "hist_evidence_clip_cap": 5.0,
            "hist_tail_agreement_threshold": 0.1,
        }
        self._aggregated_package = pkg

    def get_feature_info(self):
        return {"n_features": 3}


def test_run_prediction_multiclass_ovr_k3_writes_validation_metrics(monkeypatch, tmp_path):
    """Labeled K=3 run with OvR-style classifier stub writes validation_metrics.json."""
    output_dir = tmp_path / "pred_ovr"
    classifier_cli_main = importlib.import_module("methyl_classifier.cli.main")
    s1 = str(tmp_path / "s1")
    s2 = str(tmp_path / "s2")
    s3 = str(tmp_path / "s3")
    config = PredictorConfig(
        model_path=str(tmp_path / "ovr.pkl"),
        output_dir=str(output_dir),
        test_group_paths=[
            {"label": "g0", "paths": [s1]},
            {"label": "g1", "paths": [s2]},
            {"label": "g2", "paths": [s3]},
        ],
        sample_lineage=[
            {"absolute_path": s1, "side": "multiclass", "group_label": "g0"},
            {"absolute_path": s2, "side": "multiclass", "group_label": "g1"},
            {"absolute_path": s3, "side": "multiclass", "group_label": "g2"},
        ],
    )

    monkeypatch.setattr(
        "methyl_classifier.core.classifier.MethylClassifier",
        _DummyOvRClassifier,
    )

    def fake_classify(*, classifier, samples_list, output_file, expected_classes, **_kwargs):
        assert samples_list == [s1, s2, s3]
        assert expected_classes == [0, 1, 2]
        with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sample",
                    "prediction",
                    "expected_class",
                    "prob_class0",
                    "prob_class1",
                    "prob_class2",
                ],
            )
            writer.writeheader()
            for i, name in enumerate(["s1", "s2", "s3"]):
                p = [0.1, 0.1, 0.1]
                p[i] = 0.8
                writer.writerow(
                    {
                        "sample": name,
                        "prediction": i,
                        "expected_class": i,
                        "prob_class0": p[0],
                        "prob_class1": p[1],
                        "prob_class2": p[2],
                    }
                )

    monkeypatch.setattr(classifier_cli_main, "classify_samples_from_list", fake_classify)

    metrics = run_prediction(config)

    assert metrics["n_classes"] == 3
    assert metrics["accuracy"] == 1.0
    assert metrics["evaluation_semantics"] == "undifferentiated"
    assert Path(output_dir / "validation_metrics.json").exists()
    vm = json.loads((output_dir / "validation_metrics.json").read_text(encoding="utf-8"))
    assert vm["n_classes"] == 3
    assert len(vm["per_class"]) == 3
    assert vm["evaluation_semantics"] == "undifferentiated"


def test_run_prediction_train_holdout_binary_writes_dual_metrics(monkeypatch, tmp_path):
    """Binary config with train + holdout paths writes training_metrics, holdout_metrics, and evaluation_split column."""
    output_dir = tmp_path / "pred_th"
    classifier_cli_main = importlib.import_module("methyl_classifier.cli.main")
    tc0 = str(tmp_path / "tc0")
    td0 = str(tmp_path / "td0")
    hc0 = str(tmp_path / "hc0")
    hd0 = str(tmp_path / "hd0")
    config = PredictorConfig(
        model_path=str(tmp_path / "b.pkl"),
        output_dir=str(output_dir),
        train_control_paths=[tc0],
        train_disease_paths=[td0],
        holdout_control_paths=[hc0],
        holdout_disease_paths=[hd0],
        sample_lineage=[],
    )

    monkeypatch.setattr(
        "methyl_classifier.core.classifier.MethylClassifier",
        _DummyClassifier,
    )

    def fake_classify(*, classifier, samples_list, output_file, expected_classes, **_kwargs):
        assert samples_list == [tc0, td0, hc0, hd0]
        assert expected_classes == [0, 1, 0, 1]
        with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sample",
                    "prediction",
                    "expected_class",
                    "prob_class0",
                    "prob_class1",
                ],
            )
            writer.writeheader()
            for name, y, pred in [
                ("tc0", 0, 0),
                ("td0", 1, 1),
                ("hc0", 0, 0),
                ("hd0", 1, 1),
            ]:
                writer.writerow(
                    {
                        "sample": name,
                        "prediction": pred,
                        "expected_class": y,
                        "prob_class0": 0.9 if pred == 0 else 0.1,
                        "prob_class1": 0.1 if pred == 0 else 0.9,
                    }
                )

    monkeypatch.setattr(classifier_cli_main, "classify_samples_from_list", fake_classify)

    metrics = run_prediction(config)
    assert metrics["evaluation_semantics"] == "train_holdout"
    assert metrics["training_metrics"]["balanced_accuracy"] == 1.0
    assert metrics["holdout_metrics"]["balanced_accuracy"] == 1.0
    assert metrics["balanced_accuracy"] == 1.0

    df = pd.read_csv(output_dir / "predictions.csv")
    assert list(df["evaluation_split"]) == ["training", "training", "holdout", "holdout"]


def test_run_prediction_aggregated_ecdf_path_writes_evidence_columns(monkeypatch, tmp_path):
    output_dir = tmp_path / "pred_agg"
    s1 = str(tmp_path / "s1")
    s2 = str(tmp_path / "s2")
    config = PredictorConfig(
        model_path=str(tmp_path / "agg.pkl"),
        output_dir=str(output_dir),
        test_group_paths=[
            {"label": "healthy", "paths": [s1], "class_index": 0},
            {"label": "disease", "paths": [s2], "class_index": 1},
        ],
        sample_lineage=[],
    )

    monkeypatch.setattr(
        "methyl_classifier.core.classifier.MethylClassifier",
        _DummyAggregatedOvRClassifier,
    )

    def _fake_build_features(sample_paths, *_args, **_kwargs):
        assert sample_paths == [s1, s2]
        return SimpleNamespace(
            X=np.asarray([[0.1, 0.2, 0.1], [0.9, 0.9, 0.9]], dtype=np.float64),
            feature_names=[
                "gene_obs_fraction",
                "gene_weighted_shift_vs_healthy",
                "struct_promoter_obs_fraction",
            ],
        )

    monkeypatch.setattr(
        "methyl_validation.observed_feature_builder.build_observed_hybrid_feature_table",
        _fake_build_features,
    )

    metrics = run_prediction(config)
    assert metrics["n_classes"] == 2
    assert metrics["accuracy"] >= 0.0
    df = pd.read_csv(output_dir / "predictions.csv")
    assert "evidence_class0" in df.columns
    assert "evidence_class1" in df.columns


def test_run_prediction_ovr_k2_uses_test_group_paths_only(monkeypatch, tmp_path):
    """Binary OvR (n_classes=2) with only test_group_paths must not fall back to empty control/disease lists."""
    output_dir = tmp_path / "pred_ovr_k2"
    classifier_cli_main = importlib.import_module("methyl_classifier.cli.main")
    s0 = str(tmp_path / "c0")
    s1 = str(tmp_path / "c1")
    config = PredictorConfig(
        model_path=str(tmp_path / "ovr2.pkl"),
        output_dir=str(output_dir),
        test_control_paths=[],
        test_disease_paths=[],
        test_group_paths=[
            {"label": "all", "paths": [s0]},
            {"label": "pca", "paths": [s1]},
        ],
        sample_lineage=[],
    )

    monkeypatch.setattr(
        "methyl_classifier.core.classifier.MethylClassifier",
        _DummyClassifier,
    )

    def fake_classify(*, classifier, samples_list, output_file, expected_classes, **_kwargs):
        assert samples_list == [s0, s1]
        assert expected_classes == [0, 1]
        with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sample",
                    "prediction",
                    "expected_class",
                    "prob_class0",
                    "prob_class1",
                ],
            )
            writer.writeheader()
            for i, name in enumerate(["c0", "c1"]):
                p0, p1 = (0.8, 0.2) if i == 0 else (0.2, 0.8)
                writer.writerow(
                    {
                        "sample": name,
                        "prediction": i,
                        "expected_class": i,
                        "prob_class0": p0,
                        "prob_class1": p1,
                    }
                )

    monkeypatch.setattr(classifier_cli_main, "classify_samples_from_list", fake_classify)

    metrics = run_prediction(config)
    assert metrics["n_classes"] == 2
    assert metrics["accuracy"] == 1.0
    assert metrics["evaluation_semantics"] == "undifferentiated"


def test_predictor_config_resolves_relative_test_paths(tmp_path):
    config = PredictorConfig(
        model_path=str(tmp_path / "classifier.pkl"),
        output_dir=str(tmp_path / "out"),
        samples_base_path=str(tmp_path / "samples"),
        test_control_paths=["ctrl_1", "ctrl_2"],
        test_disease_paths=["disease_1"],
    )

    assert config.test_control_paths == [
        str((tmp_path / "samples" / "ctrl_1").resolve()),
        str((tmp_path / "samples" / "ctrl_2").resolve()),
    ]
    assert config.test_disease_paths == [str((tmp_path / "samples" / "disease_1").resolve())]


def test_run_prediction_metrics_follow_filtered_rows(monkeypatch, tmp_path):
    output_dir = tmp_path / "predictor"
    classifier_cli_main = importlib.import_module("methyl_classifier.cli.main")
    config = PredictorConfig(
        model_path=str(tmp_path / "classifier.pkl"),
        output_dir=str(output_dir),
        test_control_paths=["/samples/c0", "/samples/c1"],
        test_disease_paths=["/samples/d0"],
        comparison_label="test_comp",
        report_controls={
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": ["configs/healthy.csv"]}],
        },
        report_diseases={
            "label": "cancer",
            "groups": [{"label": "pca1", "sample_paths": ["configs/pca1.csv"]}],
        },
        sample_lineage=[
            {"absolute_path": "/samples/c0", "side": "control", "group_label": "all"},
            {"absolute_path": "/samples/c1", "side": "control", "group_label": "all"},
            {"absolute_path": "/samples/d0", "side": "disease", "group_label": "pca1"},
        ],
    )

    monkeypatch.setattr(
        "methyl_classifier.core.classifier.MethylClassifier",
        _DummyClassifier,
    )

    def fake_classify(*, classifier, samples_list, output_file, expected_classes, **_kwargs):
        assert samples_list == ["/samples/c0", "/samples/c1", "/samples/d0"]
        assert expected_classes == [0, 0, 1]
        with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sample",
                    "prediction",
                    "expected_class",
                    "prob_class0",
                    "prob_class1",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "sample": "c0",
                    "prediction": 0,
                    "expected_class": 0,
                    "prob_class0": 0.9,
                    "prob_class1": 0.1,
                }
            )
            # c1 skipped (unreadable) — only two scored rows, like MethylClassifier realignment
            writer.writerow(
                {
                    "sample": "d0",
                    "prediction": 1,
                    "expected_class": 1,
                    "prob_class0": 0.2,
                    "prob_class1": 0.8,
                }
            )

    monkeypatch.setattr(classifier_cli_main, "classify_samples_from_list", fake_classify)

    metrics = run_prediction(config)

    assert metrics["n_samples"] == 2
    assert metrics["accuracy"] == 1.0
    assert metrics["sensitivity"] == 1.0
    assert metrics["specificity"] == 1.0
    assert metrics["balanced_accuracy"] == 1.0
    assert metrics["evaluation_semantics"] == "undifferentiated"
    assert Path(output_dir / "validation_metrics.json").exists()
    report_path = output_dir / "prediction_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("mode") == "labeled"
    assert report["comparison_label"] == "test_comp"
    assert report["controls"]["label"] == "healthy"
    assert report["diseases"]["label"] == "cancer"
    ctrl_groups = report["controls"]["groups"]
    assert len(ctrl_groups) == 1 and ctrl_groups[0]["label"] == "all"
    assert len(ctrl_groups[0]["samples"]) == 1
    dis_groups = report["diseases"]["groups"]
    assert len(dis_groups[0]["samples"]) == 1
    assert dis_groups[0]["samples"][0]["prob_class1"] == 0.8


def test_run_prediction_blind_mode_writes_report_without_validation_metrics(
    monkeypatch, tmp_path
):
    """Blind cohort: no expected_class, no validation_metrics.json, mode=blind + probabilities."""
    output_dir = tmp_path / "predictor_blind"
    classifier_cli_main = importlib.import_module("methyl_classifier.cli.main")
    config = PredictorConfig(
        model_path=str(tmp_path / "classifier.pkl"),
        output_dir=str(output_dir),
        test_blind_paths=["/blind/s1", "/blind/s2"],
        report_blind={
            "label": "incoming",
            "groups": [{"label": "batch_a", "sample_paths": ["x.csv"]}],
        },
        sample_lineage=[
            {"absolute_path": "/blind/s1", "side": "blind", "group_label": "batch_a"},
            {"absolute_path": "/blind/s2", "side": "blind", "group_label": "batch_a"},
        ],
    )

    monkeypatch.setattr(
        "methyl_classifier.core.classifier.MethylClassifier",
        _DummyClassifier,
    )

    def fake_classify(*, classifier, samples_list, output_file, expected_classes, **_kwargs):
        assert samples_list == ["/blind/s1", "/blind/s2"]
        assert expected_classes is None
        with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sample",
                    "prediction",
                    "predicted_class",
                    "prob_class0",
                    "prob_class1",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "sample": "s1",
                    "prediction": 0,
                    "predicted_class": "control",
                    "prob_class0": 0.7,
                    "prob_class1": 0.3,
                }
            )
            writer.writerow(
                {
                    "sample": "s2",
                    "prediction": 1,
                    "predicted_class": "disease",
                    "prob_class0": 0.4,
                    "prob_class1": 0.6,
                }
            )

    monkeypatch.setattr(classifier_cli_main, "classify_samples_from_list", fake_classify)

    result = run_prediction(config)

    assert not Path(output_dir / "validation_metrics.json").exists()
    assert result.get("n_samples") == 2
    report_path = output_dir / "prediction_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "blind"
    assert report["validation_metrics"] is None
    assert "blind_summary" in report
    assert report["blind_summary"]["n_samples"] == 2
    assert report["blind_summary"]["predicted_counts_by_class"]["control"] == 1
    samples = report["blind"]["groups"][0]["samples"]
    assert len(samples) == 2
    assert samples[0]["probabilities"]["control"] == 0.7
    assert samples[0]["predicted_subgroup"] == "control"


def test_predictor_config_rejects_blind_with_nested_controls():
    with pytest.raises(ValueError, match="blind"):
        PredictorConfig(
            model_path="/m.pkl",
            output_dir="/out",
            controls={"label": "c", "groups": [{"label": "a", "sample_paths": []}]},
            blind={"groups": [{"label": "b", "sample_paths": []}]},
        )


def test_run_prediction_degenerate_class_and_dmp_coverage_in_metrics(
    monkeypatch, tmp_path, capsys
):
    """Labeled run with constant y_pred warns; DMP coverage columns flow into validation_metrics.json."""
    output_dir = tmp_path / "pred_deg"
    classifier_cli_main = importlib.import_module("methyl_classifier.cli.main")
    s0 = str(tmp_path / "c0")
    s1 = str(tmp_path / "c1")
    config = PredictorConfig(
        model_path=str(tmp_path / "clf.pkl"),
        output_dir=str(output_dir),
        test_control_paths=[s0],
        test_disease_paths=[s1],
    )

    monkeypatch.setattr(
        "methyl_classifier.core.classifier.MethylClassifier",
        _DummyClassifier,
    )

    def fake_classify(*, classifier, samples_list, output_file, expected_classes, **_kwargs):
        assert expected_classes == [0, 1]
        with Path(output_file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "sample",
                    "prediction",
                    "expected_class",
                    "prob_class0",
                    "prob_class1",
                    "dmps_used",
                    "dmps_total",
                    "dmp_coverage_pct",
                ],
            )
            writer.writeheader()
            for name, exp in (("c0", 0), ("c1", 1)):
                writer.writerow(
                    {
                        "sample": name,
                        "prediction": 0,
                        "expected_class": exp,
                        "prob_class0": 0.75,
                        "prob_class1": 0.25,
                        "dmps_used": 1000,
                        "dmps_total": 2000,
                        "dmp_coverage_pct": 50.0,
                    }
                )

    monkeypatch.setattr(classifier_cli_main, "classify_samples_from_list", fake_classify)

    metrics = run_prediction(config)
    err = capsys.readouterr().out
    assert "Degenerate predictions" in err

    assert metrics["evaluation_semantics"] == "undifferentiated"
    assert metrics["balanced_accuracy"] == 0.5
    assert "sample_dmp_coverage" in metrics
    cov = metrics["sample_dmp_coverage"]
    assert cov["dmp_coverage_pct_median"] == 50.0
    assert cov["dmps_used_fraction_median"] == 0.5

    vm = json.loads((output_dir / "validation_metrics.json").read_text(encoding="utf-8"))
    assert vm["sample_dmp_coverage"]["dmp_coverage_pct_median"] == 50.0


def test_predictor_config_nested_controls_diseases_expands(tmp_path):
    """Standalone JSON style: controls/diseases nested blocks fill flat paths after expand helper."""
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "s1").mkdir()
    (samples / "s2").mkdir()
    config = PredictorConfig(
        model_path=str(tmp_path / "m.pkl"),
        output_dir=str(tmp_path / "out"),
        samples_base_path=str(samples),
        controls={
            "label": "c",
            "groups": [{"label": "g1", "sample_paths": [str(samples / "s1")]}],
        },
        diseases={
            "label": "d",
            "groups": [{"label": "t1", "sample_paths": [str(samples / "s2")]}],
        },
    )
    _expand_nested_labeled_paths(config)
    assert config.test_control_paths == [str((samples / "s1").resolve())]
    assert config.test_disease_paths == [str((samples / "s2").resolve())]
    assert config.report_controls is not None
    assert config.sample_lineage[0]["group_label"] == "g1"


