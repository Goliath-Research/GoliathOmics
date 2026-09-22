"""
Multinomial logistic regression on top of native histogram pre-softmax scores.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

from .native_multiclass import NativeMulticlassHistogramClassifier


class NativeMulticlassLearnedClassifier:
    """
    Uses ``NativeMulticlassHistogramClassifier.compute_pre_softmax_scores`` as features,
    then applies a fitted sklearn multinomial ``LogisticRegression`` (and optional scaler).
    """

    def __init__(
        self,
        base: NativeMulticlassHistogramClassifier,
        logistic_regression: Any,
        feature_scaler: Any = None,
    ) -> None:
        self.base = base
        self.logistic = logistic_regression
        self.feature_scaler = feature_scaler
        self.calibrator = None

    @property
    def n_classes(self) -> int:
        return int(self.base.n_classes)

    @property
    def class_names(self) -> list[str]:
        return list(self.base.class_names)

    def set_temperature(self, temperature: float) -> None:
        self.base.set_temperature(temperature)

    def get_feature_info(self) -> Dict[str, Any]:
        return self.base.get_feature_info()

    def predict_proba(
        self,
        X: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
        debug: bool = False,
        use_gpu: bool = True,
    ) -> np.ndarray:
        del use_gpu
        scores, no_signal = self.base.compute_pre_softmax_scores(
            X, availability_mask, debug=debug
        )
        feats = np.asarray(scores, dtype=np.float64, order="C")
        if self.feature_scaler is not None:
            feats = self.feature_scaler.transform(feats)
        probs = np.asarray(self.logistic.predict_proba(feats), dtype=np.float64)
        if np.any(no_signal):
            probs = probs.copy()
            probs[no_signal, :] = 1.0 / float(self.n_classes)
        return probs

    def predict_proba_calibrated(
        self,
        X: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        return self.predict_proba(X, availability_mask=availability_mask)
