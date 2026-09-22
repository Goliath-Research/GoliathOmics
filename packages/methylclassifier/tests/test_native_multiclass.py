"""Tests for the native multiclass histogram builder and feature table."""

from __future__ import annotations

import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_classifier.core.classifier import MethylClassifier
from methyl_classifier.core.native_multiclass import NativeMulticlassHistogramClassifier
from methyl_classifier.core.native_multiclass_learned import NativeMulticlassLearnedClassifier
from methyl_classifier.models.config import ClassifierConfig
from methyl_classifier.utils.multiclass_builder import (
    NATIVE_MULTICLASS_TYPE,
    build_multiclass_model,
)
from methyl_detector.utils.multiclass_merge import merge_dmp_csvs_from_detection_dirs
from methyl_utils import MethylCentroid


def _write_dmp_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_centroid_h5(
    path: Path,
    *,
    positions: list[int],
    bin_counts: np.ndarray,
    means: list[float],
) -> None:
    n_pos = len(positions)
    means_arr = np.asarray(means, dtype=np.float64)
    sample_count = np.full(n_pos, 8, dtype=np.uint32)
    variance = np.maximum(means_arr * (1.0 - means_arr) / 20.0, 1e-3)
    sx = means_arr * sample_count.astype(np.float64)
    sx2 = variance * np.maximum(sample_count.astype(np.float64) - 1.0, 1.0)
    sx2 += (sx ** 2) / np.maximum(sample_count.astype(np.float64), 1.0)

    total_cov = np.full(n_pos, 100, dtype=np.uint32)
    sm = np.clip(np.rint(means_arr * total_cov), 1, total_cov - 1).astype(np.uint32)
    su = total_cov - sm
    df = pd.DataFrame(
        {
            "pos": np.asarray(positions, dtype=np.uint32),
            "tnc": np.zeros(n_pos, dtype=np.uint8),
            "N": sample_count,
            "Sx": sx.astype(np.float32),
            "Sx2": sx2.astype(np.float32),
            "Sm": sm,
            "Su": su,
            "Sc2": total_cov,
            "Swx2": sx2.astype(np.float32),
        }
    )
    centroid = MethylCentroid(df, metadata={"context": "CG"})
    bin_edges = np.linspace(0.0, 1.0, bin_counts.shape[1] + 1, dtype=np.float64)
    centroid.set_binned_stats(bin_edges, bin_counts.astype(np.float64))
    path.parent.mkdir(parents=True, exist_ok=True)
    centroid.save_to_h5(path, compressed=False)


def test_merge_dmp_csvs_preserves_per_comparison_feature_columns(tmp_path: Path) -> None:
    d1 = tmp_path / "detections" / "all" / "pca1"
    d2 = tmp_path / "detections" / "all" / "pca2"
    _write_dmp_csv(
        d1 / "dmps-1.csv",
        [
            {
                "chromosome": "1",
                "context": "CG",
                "position": 100,
                "mean1": 0.10,
                "mean2": 0.80,
                "delta_mean": 0.70,
                "effect_size": 0.40,
            },
            {
                "chromosome": "1",
                "context": "CG",
                "position": 200,
                "mean1": 0.20,
                "mean2": 0.60,
                "delta_mean": 0.40,
                "effect_size": 0.25,
            },
        ],
    )
    _write_dmp_csv(
        d2 / "dmps-1.csv",
        [
            {
                "chromosome": "1",
                "context": "CG",
                "position": 100,
                "mean1": 0.10,
                "mean2": 0.55,
                "delta_mean": 0.45,
                "effect_size": 0.30,
            },
            {
                "chromosome": "1",
                "context": "CG",
                "position": 300,
                "mean1": 0.30,
                "mean2": 0.85,
                "delta_mean": 0.55,
                "effect_size": 0.60,
            },
        ],
    )

    merged_path = tmp_path / "dmps-merged-multiclass.csv"
    merge_dmp_csvs_from_detection_dirs(
        [d1, d2],
        merged_path,
        weights_column="effect_size",
        detection_labels=["pca1", "pca2"],
    )

    merged = pd.read_csv(merged_path)
    assert set(merged["position"].tolist()) == {100, 200, 300}
    row100 = merged.loc[merged["position"] == 100].iloc[0]
    assert int(row100["comparison_count"]) == 2
    assert row100["comparison_labels"] == "pca1|pca2"
    assert row100["effect_size__pca1"] == 0.40
    assert row100["effect_size__pca2"] == 0.30
    assert row100["delta_mean__pca1"] == 0.70
    assert row100["delta_mean__pca2"] == 0.45
    assert row100["effect_size_sum"] == 0.70
    assert row100["effect_size_max"] == 0.40
    assert row100["weight"] == 0.70


def test_native_multiclass_contrast_mode_prefers_matching_disease_class() -> None:
    classifier = NativeMulticlassHistogramClassifier(
        positions=np.array([100, 200], dtype=np.uint32),
        bin_edges=np.array([0.0, 0.5, 1.0], dtype=np.float64),
        bin_probabilities=np.array(
            [
                [[0.9, 0.1], [0.9, 0.1]],  # control
                [[0.1, 0.9], [0.9, 0.1]],  # disease 1
                [[0.9, 0.1], [0.1, 0.9]],  # disease 2
            ],
            dtype=np.float64,
        ),
        weights=np.array(
            [
                [1.0, 1.0],
                [1.0, 1e-3],
                [1e-3, 1.0],
            ],
            dtype=np.float64,
        ),
        class_names=["control", "d1", "d2"],
        contrast_reference_class_index=0,
        score_mode="contrast_vs_control",
    )
    X = np.array([[0.1, 0.9]], dtype=np.float64)
    proba = classifier.predict_proba(X, np.ones_like(X, dtype=bool))
    assert int(np.argmax(proba[0])) == 2
    assert proba[0, 2] > proba[0, 0]
    assert proba[0, 2] > proba[0, 1]


def test_build_native_multiclass_model_loads_and_scores(tmp_path: Path) -> None:
    detection_d1 = tmp_path / "detections" / "all" / "d1"
    detection_d2 = tmp_path / "detections" / "all" / "d2"
    _write_dmp_csv(
        detection_d1 / "dmps-1.csv",
        [
            {
                "chromosome": "1",
                "context": "CG",
                "position": 100,
                "mean1": 0.10,
                "mean2": 0.85,
                "delta_mean": 0.75,
                "effect_size": 0.90,
            },
            {
                "chromosome": "1",
                "context": "CG",
                "position": 200,
                "mean1": 0.35,
                "mean2": 0.65,
                "delta_mean": 0.30,
                "effect_size": 0.80,
            },
        ],
    )
    _write_dmp_csv(
        detection_d2 / "dmps-1.csv",
        [
            {
                "chromosome": "1",
                "context": "CG",
                "position": 100,
                "mean1": 0.10,
                "mean2": 0.35,
                "delta_mean": 0.25,
                "effect_size": 0.55,
            },
            {
                "chromosome": "1",
                "context": "CG",
                "position": 200,
                "mean1": 0.35,
                "mean2": 0.90,
                "delta_mean": 0.55,
                "effect_size": 0.70,
            },
        ],
    )
    merged_path = tmp_path / "dmps-merged-multiclass.csv"
    merge_dmp_csvs_from_detection_dirs(
        [detection_d1, detection_d2],
        merged_path,
        weights_column="effect_size",
        detection_labels=["d1", "d2"],
    )

    positions = [100, 200]
    control_dir = tmp_path / "centroids" / "control"
    d1_dir = tmp_path / "centroids" / "d1"
    d2_dir = tmp_path / "centroids" / "d2"
    _write_centroid_h5(
        control_dir / "1-CG.h5",
        positions=positions,
        means=[0.10, 0.35],
        bin_counts=np.array([[50, 1, 1, 1], [1, 50, 1, 1]], dtype=np.float64),
    )
    _write_centroid_h5(
        d1_dir / "1-CG.h5",
        positions=positions,
        means=[0.85, 0.65],
        bin_counts=np.array([[1, 1, 1, 50], [1, 1, 50, 1]], dtype=np.float64),
    )
    _write_centroid_h5(
        d2_dir / "1-CG.h5",
        positions=positions,
        means=[0.35, 0.90],
        bin_counts=np.array([[1, 50, 1, 1], [1, 1, 1, 50]], dtype=np.float64),
    )

    out_pkl = tmp_path / "classifiers" / "multiclass-classifier.pkl"
    build_multiclass_model(
        {
            "dmps_csv": str(merged_path),
            "output_model": str(out_pkl),
            "weights_column": "weight",
            "control_label": "control",
            "comparison_labels": ["d1", "d2"],
            "contexts": ["CG"],
            "classes": [
                {"name": "control", "centroid_dir": str(control_dir)},
                {"name": "d1", "centroid_dir": str(d1_dir)},
                {"name": "d2", "centroid_dir": str(d2_dir)},
            ],
        }
    )

    with open(out_pkl, "rb") as handle:
        pkg = pickle.load(handle)
    assert pkg["metadata"]["classifier_type"] == NATIVE_MULTICLASS_TYPE
    assert pkg["metadata"]["score_mode"] == "contrast_vs_control"
    assert "classifier_weight" in pkg["dmp_df"].columns
    assert "classifier_weight__d1" in pkg["dmp_df"].columns
    assert "classifier_weight__d2" in pkg["dmp_df"].columns
    assert "effect_size__d1" in pkg["dmp_df"].columns
    assert "effect_size__d2" in pkg["dmp_df"].columns

    clf = MethylClassifier(ClassifierConfig(model_path=str(out_pkl)))
    X = np.array(
        [
            [0.12, 0.33],
            [0.88, 0.62],
            [0.35, 0.92],
        ],
        dtype=np.float64,
    )
    M = np.ones_like(X, dtype=bool)
    proba = clf.predict_proba(X, M)
    preds = np.argmax(proba, axis=1)

    assert proba.shape == (3, 3)
    assert clf.class_names == ["control", "d1", "d2"]
    assert clf.model_contexts == ["CG"]
    assert preds.tolist() == [0, 1, 2]


def test_compute_pre_softmax_scores_softmax_matches_predict_proba() -> None:
    clf = NativeMulticlassHistogramClassifier(
        positions=np.array([100], dtype=np.uint32),
        bin_edges=np.array([0.0, 0.5, 1.0], dtype=np.float64),
        bin_probabilities=np.array([[[0.8, 0.2]], [[0.2, 0.8]]], dtype=np.float64),
        weights=np.array([1.0]),
        class_names=["a", "b"],
        score_mode="generative",
    )
    X = np.array([[0.1], [0.9]], dtype=np.float64)
    m = np.ones_like(X, dtype=bool)
    scores, _ = clf.compute_pre_softmax_scores(X, m)
    T_eff = max(clf.temperature * math.sqrt(clf._n_effective), 0.1)
    logits = scores / T_eff
    logits -= logits.max(axis=1, keepdims=True)
    manual = np.exp(logits)
    manual /= np.maximum(manual.sum(axis=1, keepdims=True), 1e-12)
    from_clf = clf.predict_proba(X, m)
    np.testing.assert_allclose(manual, from_clf, rtol=1e-5, atol=1e-5)


def test_native_multiclass_learned_head_smoke() -> None:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    base = NativeMulticlassHistogramClassifier(
        positions=np.array([100, 200], dtype=np.uint32),
        bin_edges=np.array([0.0, 0.5, 1.0], dtype=np.float64),
        bin_probabilities=np.array(
            [
                [[0.9, 0.1], [0.9, 0.1]],
                [[0.1, 0.9], [0.9, 0.1]],
                [[0.9, 0.1], [0.1, 0.9]],
            ],
            dtype=np.float64,
        ),
        weights=np.array(
            [
                [1.0, 1.0],
                [1.0, 1e-3],
                [1e-3, 1.0],
            ],
            dtype=np.float64,
        ),
        class_names=["control", "d1", "d2"],
        contrast_reference_class_index=0,
        score_mode="contrast_vs_control",
    )
    X = np.array(
        [
            [0.1, 0.9],
            [0.9, 0.1],
            [0.5, 0.55],
        ],
        dtype=np.float64,
    )
    m = np.ones_like(X, dtype=bool)
    F, _ = base.compute_pre_softmax_scores(X, m)
    y = np.array([0, 1, 2])
    scaler = StandardScaler().fit(F)
    lr = LogisticRegression(solver="lbfgs", max_iter=2000, random_state=0)
    lr.fit(scaler.transform(F), y)
    learned = NativeMulticlassLearnedClassifier(base, lr, scaler)
    p = learned.predict_proba(X, m)
    assert p.shape == (3, 3)
    assert learned.n_classes == 3
    assert learned.class_names == ["control", "d1", "d2"]


def test_build_learned_multiclass_requires_project_path(tmp_path: Path) -> None:
    detection_d1 = tmp_path / "detections" / "all" / "d1"
    detection_d2 = tmp_path / "detections" / "all" / "d2"
    _write_dmp_csv(
        detection_d1 / "dmps-1.csv",
        [
            {
                "chromosome": "1",
                "context": "CG",
                "position": 100,
                "mean1": 0.10,
                "mean2": 0.85,
                "delta_mean": 0.75,
                "effect_size": 0.90,
            },
        ],
    )
    _write_dmp_csv(
        detection_d2 / "dmps-1.csv",
        [
            {
                "chromosome": "1",
                "context": "CG",
                "position": 100,
                "mean1": 0.10,
                "mean2": 0.35,
                "delta_mean": 0.25,
                "effect_size": 0.55,
            },
        ],
    )
    merged_path = tmp_path / "dmps-merged-multiclass.csv"
    merge_dmp_csvs_from_detection_dirs(
        [detection_d1, detection_d2],
        merged_path,
        weights_column="effect_size",
        detection_labels=["d1", "d2"],
    )
    control_dir = tmp_path / "centroids" / "control"
    d1_dir = tmp_path / "centroids" / "d1"
    d2_dir = tmp_path / "centroids" / "d2"
    _write_centroid_h5(
        control_dir / "1-CG.h5",
        positions=[100],
        means=[0.10],
        bin_counts=np.array([[50, 1, 1, 1]], dtype=np.float64),
    )
    _write_centroid_h5(
        d1_dir / "1-CG.h5",
        positions=[100],
        means=[0.85],
        bin_counts=np.array([[1, 1, 1, 50]], dtype=np.float64),
    )
    _write_centroid_h5(
        d2_dir / "1-CG.h5",
        positions=[100],
        means=[0.35],
        bin_counts=np.array([[1, 50, 1, 1]], dtype=np.float64),
    )
    out_pkl = tmp_path / "classifiers" / "multiclass-classifier.pkl"
    with pytest.raises(ValueError, match="project_path"):
        build_multiclass_model(
            {
                "dmps_csv": str(merged_path),
                "output_model": str(out_pkl),
                "weights_column": "weight",
                "control_label": "control",
                "comparison_labels": ["d1", "d2"],
                "contexts": ["CG"],
                "train_learned_multiclass": True,
                "classes": [
                    {"name": "control", "centroid_dir": str(control_dir)},
                    {"name": "d1", "centroid_dir": str(d1_dir)},
                    {"name": "d2", "centroid_dir": str(d2_dir)},
                ],
            }
        )
