"""
ECDFClassifier: Weighted log-likelihood classifier using ECDF (PCHIP) densities.

Drop-in replacement for BetaClassifier.  The prediction formula is identical:

    log p(x | class_k) = Σ_i  w_i · log F'_k_i(x_i)
    p(class_k | x) ∝ exp( log p(x | class_k) / T ) · p(class_k)

The only difference is the density model: PCHIP-derived PDF from centroid
binned_stats instead of Beta(alpha, beta).  This captures multimodal
distributions and is consistent with the DMP detection stage.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Number of grid points used to pre-compute the PDF lookup table per position.
_PDF_GRID_SIZE = 1024
# Cap per-position log PDF so a few bad positions don't dominate the mean with many DMPs (e.g. 50K+).
# log(2e-9) ≈ -20; positions with smaller PDF are treated as this when averaging.
_LOG_PDF_CAP = -20.0


class ECDFClassifier:
    """
    Per-position ECDF log-likelihood classifier.

    Each position carries a histogram (bin_counts) for each class derived from
    the centroid's binned_stats.  At prediction time the PCHIP-derived PDF is
    evaluated via a pre-computed lookup table so that no Python loops over
    samples are needed.

    Parameters
    ----------
    positions : ndarray, shape (n_dmps,)  uint32
    bin_edges : ndarray, shape (n_bins+1,)  — shared between classes
    bin_counts_c1 : ndarray, shape (n_dmps, n_bins)  — class 0 histograms
    bin_counts_c2 : ndarray, shape (n_dmps, n_bins)  — class 1 histograms
    weights : ndarray, shape (n_dmps,)  — effect_size, normalised to [1e-6, 1]
    directions : ndarray, shape (n_dmps,)  int8  — +1 hyper, -1 hypo
    temperature : float  — softmax temperature (>= 0.1)
    contexts : ndarray or None — per-DMP context label (str) for multi-context
    """

    def __init__(
        self,
        positions: np.ndarray,
        bin_edges: np.ndarray,
        bin_counts_c1: np.ndarray,
        bin_counts_c2: np.ndarray,
        weights: np.ndarray,
        directions: np.ndarray,
        temperature: float = 2.0,
        contexts: Optional[np.ndarray] = None,
        prior_c1: float = 0.5,
        prior_c2: float = 0.5,
        dependence_block_size: int = 1,
        dependence_block_shrinkage: float = 0.0,
    ):
        self.positions = np.asarray(positions, dtype=np.uint32)
        self.bin_edges = np.asarray(bin_edges, dtype=np.float64)
        self.bin_counts_c1 = np.asarray(bin_counts_c1, dtype=np.float64)
        self.bin_counts_c2 = np.asarray(bin_counts_c2, dtype=np.float64)
        self.weights = np.asarray(weights, dtype=np.float64)
        self.directions = np.asarray(directions, dtype=np.int8)
        self.temperature = max(float(temperature), 0.1)
        self.contexts = np.asarray(contexts) if contexts is not None else None
        self.n_dmps = len(self.positions)
        self.calibrator = None  # compatible with BetaClassifier interface
        self.dependence_block_size = max(1, int(dependence_block_size))
        self.dependence_block_shrinkage = min(
            1.0, max(0.0, float(dependence_block_shrinkage))
        )
        pri = np.asarray([float(prior_c1), float(prior_c2)], dtype=np.float64)
        pri = np.maximum(pri, 1e-12)
        self.class_priors = pri / np.sum(pri)

        n1, n2 = len(self.bin_counts_c1), len(self.bin_counts_c2)
        if n1 != self.n_dmps or n2 != self.n_dmps:
            raise ValueError(
                f"bin_counts shape mismatch: expected {self.n_dmps} rows, "
                f"got c1={n1}, c2={n2}"
            )

        # Effective number of positions (inverse Simpson): (sum w)^2 / sum(w^2).
        # Used to scale temperature so softmax stays well-behaved with many positions.
        w_sum = float(np.sum(self.weights))
        w_sq_sum = float(np.sum(self.weights ** 2))
        self._n_effective = (w_sum ** 2) / max(w_sq_sum, 1e-300)

        # Pre-compute PDF lookup tables on interval midpoints: (n_dmps, _PDF_GRID_SIZE - 1).
        self._grid = np.linspace(0.0, 1.0, _PDF_GRID_SIZE, dtype=np.float64)
        self._pdf_grid = (self._grid[:-1] + self._grid[1:]) / 2.0
        self._pdf_c1 = self._build_pdf_table(self.bin_counts_c1)
        self._pdf_c2 = self._build_pdf_table(self.bin_counts_c2)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _build_pdf_table(self, bin_counts: np.ndarray) -> np.ndarray:
        """
        Build a (n_dmps, len(_pdf_grid)) PDF lookup table via linear CDF interpolation.
        """
        from .array_backend import get_array_module, cdf_linear_interp_batch

        n_pos, _n_bins = bin_counts.shape
        total = np.maximum(np.sum(bin_counts, axis=1, keepdims=True), 1e-12)
        cumsum = np.cumsum(bin_counts, axis=1) / total
        cdf_at_edges = np.concatenate(
            [np.zeros((n_pos, 1), dtype=np.float64), cumsum], axis=1
        )
        xp, _ = get_array_module()
        cdf_grid = cdf_linear_interp_batch(xp, cdf_at_edges, self.bin_edges, self._grid)
        pdf_table = np.maximum(np.diff(cdf_grid, axis=1), 0.0)
        grid_mid = (self._grid[:-1] + self._grid[1:]) / 2.0
        dx = np.maximum(np.diff(self._grid), 1e-12)
        pdf_table = pdf_table / dx[np.newaxis, :]
        trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")
        area = trapz(pdf_table, grid_mid, axis=1)
        area = np.maximum(area, 1e-12)
        pdf_table /= area[:, np.newaxis]
        return np.maximum(pdf_table, 1e-300)

    def _interp_pdf_rows(self, x_col: np.ndarray, pdf_row: np.ndarray) -> np.ndarray:
        from .array_backend import get_array_module, linear_interp_on_grid

        xp, _ = get_array_module()
        return linear_interp_on_grid(xp, x_col, self._pdf_grid, pdf_row)

    # ------------------------------------------------------------------
    # Public interface (mirrors BetaClassifier)
    # ------------------------------------------------------------------

    def set_temperature(self, temperature: float) -> None:
        self.temperature = max(float(temperature), 0.1)

    def set_class_priors(self, prior_c1: float, prior_c2: float) -> None:
        """Set class priors used by posterior prediction."""
        pri = np.asarray([float(prior_c1), float(prior_c2)], dtype=np.float64)
        pri = np.maximum(pri, 1e-12)
        self.class_priors = pri / np.sum(pri)

    def compute_log_pdf_matrices(
        self,
        X: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Precompute per-sample, per-feature log PDF values for both classes.

        Returns
        -------
        (log_p_c1, log_p_c2, availability_mask)
            Each log-PDF matrix has shape (n_samples, n_dmps).
        """
        if X.shape[1] != self.n_dmps:
            raise ValueError(
                f"ECDFClassifier expects {self.n_dmps} features, got {X.shape[1]}"
            )

        if availability_mask is not None:
            avail = np.asarray(availability_mask, dtype=bool)
        else:
            avail = np.isfinite(X)

        X_clean = np.where(avail, X, 0.5)
        X_clean = np.clip(X_clean, 1e-7, 1.0 - 1e-7)
        n_samples = X.shape[0]
        log_p_c1 = np.zeros((n_samples, self.n_dmps), dtype=np.float64)
        log_p_c2 = np.zeros((n_samples, self.n_dmps), dtype=np.float64)

        for i in range(self.n_dmps):
            pdf1_vals = self._interp_pdf_rows(X_clean[:, i], self._pdf_c1[i])
            pdf2_vals = self._interp_pdf_rows(X_clean[:, i], self._pdf_c2[i])
            log_p_c1[:, i] = np.log(np.maximum(pdf1_vals, 1e-300))
            log_p_c2[:, i] = np.log(np.maximum(pdf2_vals, 1e-300))

        log_p_c1 = np.where(avail, log_p_c1, 0.0)
        log_p_c2 = np.where(avail, log_p_c2, 0.0)
        return log_p_c1, log_p_c2, avail

    def _aggregate_weighted_log_likelihood(
        self,
        log_p: np.ndarray,
        avail: np.ndarray,
    ) -> np.ndarray:
        """
        Aggregate per-feature log-likelihoods.

        Default (block_size=1): weighted mean over features.
        Dependence-aware mode (block_size>1): compute block means and combine with
        sqrt(block_weight) to damp over-confidence from dense correlated loci.
        """
        w = self.weights[np.newaxis, :]  # (1, n_dmps)
        w_avail = np.where(avail, w, 0.0)
        w_sum = np.sum(w_avail, axis=1)
        w_sum = np.maximum(w_sum, 1e-12)
        global_mean = np.sum(w_avail * log_p, axis=1) / w_sum

        if self.dependence_block_size <= 1:
            return global_mean

        bs = int(self.dependence_block_size)
        n = int(self.n_dmps)
        block_num = np.zeros(log_p.shape[0], dtype=np.float64)
        block_den = np.zeros(log_p.shape[0], dtype=np.float64)
        sh = float(self.dependence_block_shrinkage)
        for start in range(0, n, bs):
            stop = min(start + bs, n)
            sl = slice(start, stop)
            bw = np.sum(w_avail[:, sl], axis=1)
            bw_safe = np.maximum(bw, 1e-12)
            bmean = np.sum(w_avail[:, sl] * log_p[:, sl], axis=1) / bw_safe
            if sh > 0.0:
                bmean = (1.0 - sh) * bmean + sh * global_mean
            # Effective weight dampening to reduce correlation-driven overcounting.
            beff = np.sqrt(np.maximum(bw, 0.0))
            block_num += beff * bmean
            block_den += beff
        block_den = np.maximum(block_den, 1e-12)
        return block_num / block_den

    def predict_proba(
        self,
        X: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
        debug: bool = False,
        use_gpu: bool = True,
    ) -> np.ndarray:
        """
        Predict posterior probabilities.

        Parameters
        ----------
        X : ndarray, shape (n_samples, n_dmps)
            Methylation fractions in [0, 1].  NaN = position unavailable.
        availability_mask : ndarray bool, shape (n_samples, n_dmps), optional
            If provided, overrides the NaN-based availability detection.

        Returns
        -------
        ndarray, shape (n_samples, 2)  — P(class0), P(class1) for each sample.
        """
        n_samples = X.shape[0]
        log_p_c1, log_p_c2, avail = self.compute_log_pdf_matrices(
            X,
            availability_mask=availability_mask,
        )
        # Cap per-position log PDF so a minority of floor positions don't dominate the mean with many DMPs
        log_p_c1 = np.maximum(log_p_c1, _LOG_PDF_CAP)
        log_p_c2 = np.maximum(log_p_c2, _LOG_PDF_CAP)

        # Weighted likelihood aggregation (independent-loci baseline or optional
        # dependence-aware block aggregation).
        sum_ll_c1 = self._aggregate_weighted_log_likelihood(log_p_c1, avail)
        sum_ll_c2 = self._aggregate_weighted_log_likelihood(log_p_c2, avail)

        # Zero out samples with no valid positions
        valid_counts = np.sum(avail, axis=1)
        no_valid = valid_counts == 0
        sum_ll_c1[no_valid] = 0.0
        sum_ll_c2[no_valid] = 0.0

        if debug:
            logger.debug(
                "ECDFClassifier.predict_proba: %d samples, %d DMPs",
                n_samples,
                self.n_dmps,
            )
            logger.debug(
                "  valid positions range: %d – %d",
                int(valid_counts.min()),
                int(valid_counts.max()),
            )
            logger.debug(
                "  log-likelihood sample 0: c1=%.3f, c2=%.3f",
                float(sum_ll_c1[0]),
                float(sum_ll_c2[0]),
            )

        # Temperature scaled by effective number of positions so many positions don't over-sharpen
        T_eff = self.temperature * math.sqrt(self._n_effective)
        T_eff = min(T_eff, 10.0)  # cap to avoid numerical issues
        # Temperature-scaled posterior log-scores: likelihood + log-prior.
        log_priors = np.log(np.maximum(self.class_priors, 1e-300))[np.newaxis, :]
        log_likes = (np.stack([sum_ll_c1, sum_ll_c2], axis=1) / T_eff) + log_priors
        log_likes -= log_likes.max(axis=1, keepdims=True)
        probs = np.exp(log_likes)
        probs /= probs.sum(axis=1, keepdims=True)
        return probs

    def predict_proba_calibrated(
        self,
        X: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Predict with Platt calibration if fitted, else raw predict_proba."""
        if self.calibrator is not None:
            raw = self.predict_proba(X, availability_mask)
            try:
                from sklearn.preprocessing import StandardScaler as _SS  # noqa: F401
                raw_c1 = raw[:, 1].reshape(-1, 1)
                if hasattr(self, "calibrator_scaler") and self.calibrator_scaler is not None:
                    raw_c1 = self.calibrator_scaler.transform(raw_c1)
                cal = self.calibrator.predict_proba(raw_c1)
                return cal
            except Exception:
                return raw
        return self.predict_proba(X, availability_mask)

    def calibrate_platt(
        self,
        X: np.ndarray,
        y: np.ndarray,
        availability_mask: Optional[np.ndarray] = None,
    ) -> None:
        """Fit a Platt scaling calibrator on calibration data."""
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            logger.warning("sklearn not available; Platt calibration skipped")
            return
        raw = self.predict_proba(X, availability_mask)
        raw_c1 = raw[:, 1].reshape(-1, 1)
        scaler = StandardScaler()
        raw_scaled = scaler.fit_transform(raw_c1)
        lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=300)
        lr.fit(raw_scaled, y)
        self.calibrator = lr
        self.calibrator_scaler = scaler

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Save classifier to a compressed .npz archive."""
        path = str(path)
        if not path.endswith(".npz"):
            path += ".npz"
        save_kwargs: Dict[str, Any] = dict(
            positions=self.positions,
            bin_edges=self.bin_edges,
            bin_counts_c1=self.bin_counts_c1,
            bin_counts_c2=self.bin_counts_c2,
            weights=self.weights,
            directions=self.directions,
            temperature=np.array([self.temperature]),
            class_priors=np.asarray(self.class_priors, dtype=np.float64),
            dependence_block_size=np.array([self.dependence_block_size], dtype=np.int32),
            dependence_block_shrinkage=np.array(
                [self.dependence_block_shrinkage], dtype=np.float64
            ),
        )
        if self.contexts is not None:
            # Store as fixed-length bytes for portability
            save_kwargs["contexts"] = np.asarray(
                [c.encode("utf-8") for c in self.contexts]
            )
        np.savez_compressed(path, **save_kwargs)
        logger.info("ECDFClassifier saved to %s", path)

    @classmethod
    def load(cls, path: str) -> "ECDFClassifier":
        """Load a classifier previously saved with :meth:`save`."""
        path = str(path)
        if not path.endswith(".npz"):
            path += ".npz"
        d = np.load(path, allow_pickle=False)
        contexts = None
        if "contexts" in d:
            contexts = np.asarray([c.decode("utf-8") for c in d["contexts"]])
        priors = np.asarray(d["class_priors"], dtype=np.float64) if "class_priors" in d else np.array([0.5, 0.5], dtype=np.float64)
        dep_block_size = int(d["dependence_block_size"][0]) if "dependence_block_size" in d else 1
        dep_block_shrink = float(d["dependence_block_shrinkage"][0]) if "dependence_block_shrinkage" in d else 0.0
        return cls(
            positions=d["positions"],
            bin_edges=d["bin_edges"],
            bin_counts_c1=d["bin_counts_c1"],
            bin_counts_c2=d["bin_counts_c2"],
            weights=d["weights"],
            directions=d["directions"],
            temperature=float(d["temperature"][0]),
            contexts=contexts,
            prior_c1=float(priors[0]),
            prior_c2=float(priors[1]),
            dependence_block_size=dep_block_size,
            dependence_block_shrinkage=dep_block_shrink,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_dataframe(
        cls,
        dmpDF: pd.DataFrame,
        bin_edges: np.ndarray,
        bin_counts_c1: np.ndarray,
        bin_counts_c2: np.ndarray,
        temperature: float = 2.0,
        prior_c1: float = 0.5,
        prior_c2: float = 0.5,
        dependence_block_size: int = 1,
        dependence_block_shrinkage: float = 0.0,
    ) -> "ECDFClassifier":
        """
        Create an ECDFClassifier from a DMP DataFrame and centroid histograms.

        Parameters
        ----------
        dmpDF : DataFrame with columns ``pos``, ``weight``.
            Optional: ``delta_sign`` (int8) and ``context`` (str).
        bin_edges : shape (n_bins+1,) — shared between centroids.
        bin_counts_c1 : shape (n_dmps, n_bins) — class 0 histograms.
            Row order must match the row order of ``dmpDF``.
        bin_counts_c2 : shape (n_dmps, n_bins) — class 1 histograms.
        temperature : softmax temperature.
        """
        required = ["pos", "weight"]
        missing = [c for c in required if c not in dmpDF.columns]
        if missing:
            raise ValueError(f"dmpDF missing required columns: {missing}")

        positions = np.asarray(dmpDF["pos"].values, dtype=np.uint32)
        weights = np.asarray(dmpDF["weight"].values, dtype=np.float64)

        if "delta_sign" in dmpDF.columns:
            directions = np.asarray(dmpDF["delta_sign"].values, dtype=np.int8)
        elif "mean1" in dmpDF.columns and "mean2" in dmpDF.columns:
            directions = np.sign(
                dmpDF["mean1"].values - dmpDF["mean2"].values
            ).astype(np.int8)
        else:
            directions = np.ones(len(positions), dtype=np.int8)

        contexts = (
            np.asarray(dmpDF["context"].values, dtype=str)
            if "context" in dmpDF.columns
            else None
        )

        return cls(
            positions=positions,
            bin_edges=np.asarray(bin_edges, dtype=np.float64),
            bin_counts_c1=np.asarray(bin_counts_c1, dtype=np.float64),
            bin_counts_c2=np.asarray(bin_counts_c2, dtype=np.float64),
            weights=weights,
            directions=directions,
            temperature=temperature,
            contexts=contexts,
            prior_c1=prior_c1,
            prior_c2=prior_c2,
            dependence_block_size=dependence_block_size,
            dependence_block_shrinkage=dependence_block_shrinkage,
        )

    # ------------------------------------------------------------------
    # Feature info (for MethylClassifier / pipeline compatibility)
    # ------------------------------------------------------------------

    def get_feature_info(self) -> Dict[str, Any]:
        """Return feature info for extraction and display (positions, n_features)."""
        return {
            "positions": np.asarray(self.positions, dtype=np.uint32),
            "n_features": int(self.n_dmps),
        }

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ECDFClassifier(n_dmps={self.n_dmps}, "
            f"n_bins={len(self.bin_edges)-1}, "
            f"temperature={self.temperature:.2f}, "
            f"priors=({self.class_priors[0]:.3f},{self.class_priors[1]:.3f}), "
            f"dep_block={self.dependence_block_size}, "
            f"dep_shrink={self.dependence_block_shrinkage:.2f})"
        )
