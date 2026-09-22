"""Stratified holdout for isotonic calibration and chromosome stacking."""

import numpy as np
import pytest

from methyl_classifier.core.classifier import MethylClassifier
from methyl_classifier.models.config import ClassifierConfig
from methyl_classifier.utils.calibration_split import stratified_calibration_fit_mask


def test_stratified_calibration_fit_mask_per_class_floor():
    y = np.array([0, 0, 0, 1, 1, 1])
    m = stratified_calibration_fit_mask(y, train_fraction=0.5, seed=0)
    assert m.sum() == 2
    assert (~m).sum() == 4
    assert set(np.unique(y[m])) == {0, 1}


def test_stratified_calibration_fit_mask_more_train_when_fraction_high():
    y = np.array([0, 0, 0, 1, 1, 1])
    m = stratified_calibration_fit_mask(y, train_fraction=0.9, seed=0)
    assert m.sum() == 4
    assert (~m).sum() == 2


def test_stratified_calibration_fit_mask_degenerate_returns_all_true():
    y = np.array([0, 1])
    m = stratified_calibration_fit_mask(y, train_fraction=0.99, seed=1)
    assert m.all()


def test_calibrate_probabilities_fit_mask_uses_subset():
    cfg = ClassifierConfig(
        model_path="/nonexistent.pkl",
        use_isotonic_calibration=True,
    )
    mc = object.__new__(MethylClassifier)
    mc.config = cfg
    mc.metadata = {}

    probas = np.array(
        [[0.9, 0.1], [0.1, 0.9], [0.5, 0.5], [0.4, 0.6]],
        dtype=np.float64,
    )
    y = np.array([0, 0, 1, 1])
    fit_mask = np.array([True, False, True, False])

    out_partial = mc.calibrate_probabilities(probas.copy(), y, fit_mask=fit_mask)

    mc2 = object.__new__(MethylClassifier)
    mc2.config = cfg
    mc2.metadata = {}
    out_full = mc2.calibrate_probabilities(probas.copy(), y, fit_mask=None)

    assert out_partial.shape == probas.shape
    assert out_full.shape == probas.shape
    assert not np.allclose(out_partial, out_full)


def test_fit_chromosome_weights_fit_row_mask_wrong_length_raises():
    cfg = ClassifierConfig(model_path="/nonexistent.pkl")
    mc = object.__new__(MethylClassifier)
    mc.config = cfg
    mc.is_multi_chromosome = True
    mc.classifiers = {"1": None, "2": None}

    X = np.random.default_rng(0).random((4, 2))
    y = np.array([0, 0, 1, 1], dtype=np.float64)
    with pytest.raises(ValueError, match="fit_row_mask length"):
        mc.fit_chromosome_weights(
            X,
            y,
            method="linear",
            regularization="none",
            fit_row_mask=np.array([True, False]),
        )


def test_fit_chromosome_weights_fit_row_mask_runs():
    cfg = ClassifierConfig(model_path="/nonexistent.pkl")
    mc = object.__new__(MethylClassifier)
    mc.config = cfg
    mc.is_multi_chromosome = True
    mc.classifiers = {"1": None, "2": None}

    X = np.array(
        [
            [0.2, 0.8],
            [0.3, 0.7],
            [0.7, 0.3],
            [0.8, 0.2],
        ],
        dtype=np.float64,
    )
    y = np.array([0, 0, 1, 1], dtype=np.float64)
    fm = np.array([True, False, True, False])
    mc.fit_chromosome_weights(X, y, method="linear", regularization="none", fit_row_mask=fm)
    w = mc.chromosome_weights
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert min(w.values()) >= 0
