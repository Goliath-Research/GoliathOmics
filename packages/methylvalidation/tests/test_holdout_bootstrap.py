"""Tests for held-out batch bootstrap QC-metric distributions."""

from __future__ import annotations

import numpy as np
import pytest

from methyl_validation.holdout_bootstrap import (
    BINARY_METRIC_KEYS,
    MULTICLASS_METRIC_KEYS,
    bootstrap_holdout_metrics,
    compute_metrics_once,
)


def test_binary_point_metrics_match_hand_computation():
    # 2 control (0), 2 disease (1). Predictions: one error per class.
    y_true = np.array([0, 0, 1, 1], dtype=int)
    y_pred = np.array([0, 1, 1, 0], dtype=int)
    m = compute_metrics_once(y_true, y_pred, None, n_classes=2, control_index=0)
    assert m["sensitivity"] == pytest.approx(0.5)
    assert m["specificity"] == pytest.approx(0.5)
    assert m["balanced_accuracy"] == pytest.approx(0.5)
    assert np.isnan(m["auc"])  # no probabilities supplied


def test_binary_auc_perfect_separation():
    y_true = np.array([0, 0, 1, 1], dtype=int)
    y_pred = np.array([0, 0, 1, 1], dtype=int)
    # prob of disease (class 1) cleanly separates classes.
    proba = np.array([[0.9, 0.1], [0.8, 0.2], [0.2, 0.8], [0.1, 0.9]], dtype=float)
    m = compute_metrics_once(y_true, y_pred, proba, n_classes=2, control_index=0)
    assert m["auc"] == pytest.approx(1.0)
    assert m["balanced_accuracy"] == pytest.approx(1.0)


def test_bootstrap_binary_shape_and_keys():
    rng = np.random.default_rng(0)
    y_true = np.array([0] * 20 + [1] * 20, dtype=int)
    y_pred = y_true.copy()
    # Flip a few to make metrics < 1.
    y_pred[:3] = 1
    y_pred[-3:] = 0
    proba = np.zeros((40, 2), dtype=float)
    proba[y_true == 0] = [0.8, 0.2]
    proba[y_true == 1] = [0.2, 0.8]
    res = bootstrap_holdout_metrics(
        y_true, y_pred, proba, n_classes=2, control_index=0, n_bootstrap=200, ci=0.95, seed=42
    )
    assert res["n_samples"] == 40
    assert res["n_classes"] == 2
    assert set(res["metric_keys"]) == set(BINARY_METRIC_KEYS)
    assert len(res["samples"]) == 200
    for key in BINARY_METRIC_KEYS:
        b = res["bootstrap"][key]
        assert b["n_valid"] > 0
        assert b["ci_low"] <= b["mean"] <= b["ci_high"] + 1e-9


def test_bootstrap_is_deterministic_with_seed():
    y_true = np.array([0] * 10 + [1] * 10, dtype=int)
    y_pred = y_true.copy()
    a = bootstrap_holdout_metrics(y_true, y_pred, n_classes=2, n_bootstrap=50, seed=7)
    b = bootstrap_holdout_metrics(y_true, y_pred, n_classes=2, n_bootstrap=50, seed=7)
    assert a["bootstrap"]["balanced_accuracy"]["mean"] == b["bootstrap"]["balanced_accuracy"]["mean"]


def test_multiclass_emits_screening_and_macro_keys():
    y_true = np.array([0] * 10 + [1] * 8 + [2] * 8, dtype=int)
    y_pred = y_true.copy()
    y_pred[:2] = 1  # a couple control mislabeled as disease
    res = bootstrap_holdout_metrics(
        y_true, y_pred, n_classes=3, control_index=0, disease_indices=[1, 2], n_bootstrap=100, seed=1
    )
    assert res["n_classes"] == 3
    assert set(res["metric_keys"]) == set(MULTICLASS_METRIC_KEYS)
    assert "screening_sensitivity" in res["bootstrap"]
    assert "macro_recall" in res["bootstrap"]


def test_empty_holdout_raises():
    with pytest.raises(ValueError):
        bootstrap_holdout_metrics(np.array([], dtype=int), np.array([], dtype=int), n_classes=2)


def test_auc_nan_when_single_class_present():
    # All samples are control -> AUC undefined, should be NaN (not crash).
    y_true = np.array([0, 0, 0], dtype=int)
    y_pred = np.array([0, 0, 1], dtype=int)
    proba = np.array([[0.7, 0.3], [0.6, 0.4], [0.4, 0.6]], dtype=float)
    m = compute_metrics_once(y_true, y_pred, proba, n_classes=2, control_index=0)
    assert np.isnan(m["auc"])
