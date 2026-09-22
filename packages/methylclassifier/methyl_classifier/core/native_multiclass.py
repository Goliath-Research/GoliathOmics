"""
Native multiclass classifier over a shared union DMP table.

The model stores one centroid-derived methylation histogram per class and DMP.
At prediction time, each observed methylation value is mapped to a histogram bin,
the per-class bin probability is looked up, and weighted mean log-likelihoods are
softmaxed into posterior class probabilities.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_LOG_BIN_PROB_CAP = -20.0


class NativeMulticlassHistogramClassifier:
    """
    Native K-class histogram scorer using centroid ECDF histograms.

    Parameters
    ----------
    positions:
        Genomic positions for the union DMP table, in classifier feature order.
    bin_edges:
        Shared histogram bin edges in ``[0, 1]``.
    bin_probabilities:
        Array of shape ``(n_classes, n_dmps, n_bins)`` with row-normalized
        per-class bin probabilities for each DMP.
    weights:
        Positive feature weights, typically derived from aggregated effect sizes.
    class_names:
        Ordered class labels matching the first axis of ``bin_probabilities``.
    temperature:
        Softmax temperature multiplier. A small effective temperature sharpening
        is applied based on the effective number of weighted DMPs.
    """

    def __init__(
        self,
        positions: np.ndarray,
        bin_edges: np.ndarray,
        bin_probabilities: np.ndarray,
        weights: np.ndarray,
        class_names: list[str],
        temperature: float = 1.0,
        contrast_reference_class_index: Optional[int] = None,
        score_mode: str = "generative",
    ) -> None:
        self.positions = np.asarray(positions, dtype=np.uint32)
        self.bin_edges = np.asarray(bin_edges, dtype=np.float64)
        self.bin_probabilities = np.asarray(bin_probabilities, dtype=np.float64)
        self.class_names = list(class_names)
        self.n_classes = len(self.class_names)
        self.classes_ = np.arange(self.n_classes, dtype=np.int32)
        self.temperature = max(float(temperature), 0.1)
        self.score_mode = str(score_mode or "generative")
        self.contrast_reference_class_index = (
            None
            if contrast_reference_class_index is None
            else int(contrast_reference_class_index)
        )
        self.calibrator = None

        if self.positions.ndim != 1:
            raise ValueError("positions must be a 1D array")
        if self.bin_edges.ndim != 1 or len(self.bin_edges) < 2:
            raise ValueError("bin_edges must be a 1D array with at least 2 entries")
        if self.bin_probabilities.ndim != 3:
            raise ValueError(
                "bin_probabilities must have shape (n_classes, n_dmps, n_bins)"
            )
        if self.bin_probabilities.shape[0] != self.n_classes:
            raise ValueError(
                "bin_probabilities first dimension must match len(class_names)"
            )
        if self.bin_probabilities.shape[1] != len(self.positions):
            raise ValueError(
                "bin_probabilities second dimension must match len(positions)"
            )
        if self.bin_probabilities.shape[2] != len(self.bin_edges) - 1:
            raise ValueError(
                "bin_probabilities third dimension must match len(bin_edges) - 1"
            )
        raw_weights = np.asarray(weights, dtype=np.float64)
        if raw_weights.ndim == 1:
            if raw_weights.shape != self.positions.shape:
                raise ValueError("1D weights must match positions shape")
            class_weights = np.tile(raw_weights[np.newaxis, :], (self.n_classes, 1))
        elif raw_weights.ndim == 2:
            if raw_weights.shape != (self.n_classes, len(self.positions)):
                raise ValueError(
                    "2D weights must have shape (n_classes, n_positions)"
                )
            class_weights = raw_weights
        else:
            raise ValueError("weights must be 1D or 2D")

        self.n_dmps = int(len(self.positions))
        self.n_bins = int(self.bin_probabilities.shape[2])
        self.class_weights = np.where(
            np.isfinite(class_weights) & (class_weights > 0.0),
            class_weights,
            1e-6,
        )
        self.weights = np.mean(self.class_weights, axis=0)
        row_sums = np.sum(self.bin_probabilities, axis=2, keepdims=True)
        row_sums = np.where(row_sums > 0.0, row_sums, 1.0)
        self.bin_probabilities = np.clip(self.bin_probabilities / row_sums, 1e-300, 1.0)

        w_sum = float(np.sum(self.weights))
        w_sq_sum = float(np.sum(self.weights ** 2))
        self._n_effective = (w_sum ** 2) / max(w_sq_sum, 1e-300)
        if self.contrast_reference_class_index is not None:
            if not (0 <= self.contrast_reference_class_index < self.n_classes):
                raise ValueError("contrast_reference_class_index out of range")
            if self.score_mode != "contrast_vs_control":
                self.score_mode = "contrast_vs_control"

    def set_temperature(self, temperature: float) -> None:
        self.temperature = max(float(temperature), 0.1)

    def get_feature_info(self) -> Dict[str, Any]:
        return {
            "positions": self.positions.copy(),
            "n_features": self.n_dmps,
        }

    def _effective_temperature(self) -> float:
        return min(self.temperature * math.sqrt(self._n_effective), 10.0)

    def compute_pre_softmax_scores(
        self,
        X: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
        *,
        debug: bool = False,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Weighted log-likelihood scores per class (same tensor softmaxed in ``predict_proba``),
        before temperature and softmax. Second return value is a boolean mask (n_samples,) True
        where no class had usable weighted signal.
        """
        X = np.asarray(X, dtype=np.float64)
        if X.ndim != 2 or X.shape[1] != self.n_dmps:
            raise ValueError(
                f"NativeMulticlassHistogramClassifier expects shape (n, {self.n_dmps}), got {X.shape}"
            )

        if availability_mask is not None:
            avail = np.asarray(availability_mask, dtype=bool)
            if avail.shape != X.shape:
                raise ValueError(
                    f"availability_mask shape {avail.shape} must match X shape {X.shape}"
                )
        else:
            avail = np.isfinite(X)

        n_samples = int(X.shape[0])
        if n_samples == 0:
            return (
                np.zeros((0, self.n_classes), dtype=np.float64),
                np.zeros((0,), dtype=bool),
            )

        X_clean = np.where(avail, X, 0.5)
        X_clean = np.clip(X_clean, 0.0, 1.0)
        bin_idx = np.searchsorted(self.bin_edges, X_clean, side="right") - 1
        bin_idx = np.clip(bin_idx, 0, self.n_bins - 1).astype(np.intp, copy=False)

        lookup_idx = bin_idx[:, :, np.newaxis]
        log_prob_tables = []
        for class_idx in range(self.n_classes):
            probs = np.take_along_axis(
                self.bin_probabilities[class_idx][np.newaxis, :, :],
                lookup_idx,
                axis=2,
            ).squeeze(axis=2)
            log_probs = np.log(np.maximum(probs, 1e-300))
            log_probs = np.maximum(log_probs, _LOG_BIN_PROB_CAP)
            log_probs = np.where(avail, log_probs, 0.0)
            log_prob_tables.append(log_probs)

        scores = np.zeros((n_samples, self.n_classes), dtype=np.float64)
        score_has_signal = np.zeros((n_samples, self.n_classes), dtype=bool)
        if (
            self.score_mode == "contrast_vs_control"
            and self.contrast_reference_class_index is not None
            and self.n_classes >= 2
        ):
            ref_idx = self.contrast_reference_class_index
            ref_log_probs = log_prob_tables[ref_idx]
            disease_scores = []
            for class_idx in range(self.n_classes):
                if class_idx == ref_idx:
                    continue
                w = self.class_weights[class_idx][np.newaxis, :]
                w_avail = np.where(avail, w, 0.0)
                w_sum = np.sum(w_avail, axis=1)
                denom = np.maximum(w_sum, 1e-12)
                contrast_log_probs = log_prob_tables[class_idx] - ref_log_probs
                scores[:, class_idx] = np.sum(w_avail * contrast_log_probs, axis=1) / denom
                score_has_signal[:, class_idx] = w_sum > 0.0
                disease_scores.append(scores[:, class_idx])
            if disease_scores:
                disease_scores_arr = np.stack(disease_scores, axis=1)
                scores[:, ref_idx] = -np.max(disease_scores_arr, axis=1)
                score_has_signal[:, ref_idx] = np.any(
                    score_has_signal[:, np.arange(self.n_classes) != ref_idx], axis=1
                )
        else:
            for class_idx in range(self.n_classes):
                w = self.class_weights[class_idx][np.newaxis, :]
                w_avail = np.where(avail, w, 0.0)
                w_sum = np.sum(w_avail, axis=1)
                denom = np.maximum(w_sum, 1e-12)
                scores[:, class_idx] = np.sum(w_avail * log_prob_tables[class_idx], axis=1) / denom
                score_has_signal[:, class_idx] = w_sum > 0.0

        no_signal = ~np.any(score_has_signal, axis=1)
        if np.any(no_signal):
            scores = scores.copy()
            scores[no_signal, :] = 0.0

        if debug:
            valid_counts = np.sum(avail, axis=1)
            logger.debug(
                "NativeMulticlassHistogramClassifier.compute_pre_softmax_scores: "
                "%d samples, %d DMPs, %d classes",
                n_samples,
                self.n_dmps,
                self.n_classes,
            )
            logger.debug("  score_mode: %s", self.score_mode)
            logger.debug(
                "  valid positions range: %d - %d",
                int(valid_counts.min()) if valid_counts.size else 0,
                int(valid_counts.max()) if valid_counts.size else 0,
            )
            logger.debug(
                "  score sample 0: %s",
                {
                    self.class_names[i]: float(scores[0, i])
                    for i in range(self.n_classes)
                }
                if n_samples > 0
                else {},
            )

        return scores, no_signal

    def predict_proba(
        self,
        X: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
        debug: bool = False,
        use_gpu: bool = True,
    ) -> np.ndarray:
        del use_gpu
        scores, no_signal = self.compute_pre_softmax_scores(
            X, availability_mask, debug=debug
        )
        n_samples = int(scores.shape[0])
        if n_samples == 0:
            return np.zeros((0, self.n_classes), dtype=np.float64)

        T_eff = max(self._effective_temperature(), 0.1)
        logits = scores / T_eff
        logits -= logits.max(axis=1, keepdims=True)
        probs = np.exp(logits)
        probs /= np.maximum(probs.sum(axis=1, keepdims=True), 1e-12)
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
