# methyl_utils/core/distribution_views.py
"""
Distribution views for methylation centroids: common protocol (parameters, mean, overlap)
and ECDF-only implementation. All handle edge cases where methylation level is 0 (mC=0) or 1 (uC=0).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Union

import numpy as np

try:
    from scipy.interpolate import PchipInterpolator
except ImportError:
    PchipInterpolator = None  # type: ignore

try:
    from .methyl_frame import MethylCentroid, MethylSample
except ImportError:
    MethylCentroid = None  # type: ignore
    MethylSample = None  # type: ignore

MIN_EPS = 1e-12


class MethylDistributionView(Protocol):
    """Protocol: parameters, mean, and overlap(other)."""

    @property
    def parameters(self) -> Dict[str, Any]:
        """Distribution parameters (e.g. bin_edges, bin_counts; or mu, sigma2)."""
        ...

    @property
    def mean(self) -> np.ndarray:
        """Per-position mean (0/1 safe)."""
        ...

    def overlap(self, other: "MethylDistributionView") -> np.ndarray:
        """Overlap with another view; returns array in [0, 1]."""
        ...


def _clip_proportion(p: np.ndarray, eps: float = MIN_EPS) -> np.ndarray:
    return np.clip(np.asarray(p, dtype=np.float64), eps, 1.0 - eps)


def _safe_mean_numer(a: np.ndarray, b: np.ndarray, eps: float = MIN_EPS) -> np.ndarray:
    s = np.maximum(np.asarray(a, dtype=np.float64) + np.asarray(b, dtype=np.float64), eps)
    return np.where(s > 0, np.asarray(a, dtype=np.float64) / s, 0.5)


# --- Counts view (proportions from mC, uC) ---
class CountsView:
    """Count-based view: parameters = (mC, uC), mean = mC/(mC+uC), 0/1 safe."""

    def __init__(self, mC: np.ndarray, uC: np.ndarray):
        self._mC = np.asarray(mC, dtype=np.uint32)
        self._uC = np.asarray(uC, dtype=np.uint32)
        cov = self._mC.astype(np.float64) + self._uC.astype(np.float64)
        self._mean = np.where(cov > 0, self._mC.astype(np.float64) / cov, 0.5)

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"mC": self._mC, "uC": self._uC}

    @property
    def mean(self) -> np.ndarray:
        return self._mean

    def overlap(self, other: MethylDistributionView) -> np.ndarray:
        if isinstance(other, CountsView):
            n = min(len(self._mean), len(other.mean))
            return np.maximum(0, 1 - np.abs(self._mean[:n] - other.mean[:n]))
        # Generic fallback: use other's mean and 1 - |p1 - p2| as proxy
        om = np.asarray(other.mean, dtype=np.float64)
        n = min(len(self._mean), len(om))
        return np.clip(1 - np.abs(self._mean[:n] - om[:n]), 0, 1)


# --- Normal view (mu, sigma2 from Sx, Sx2, N) ---
class NormalView:
    """Normal view: parameters = (mu, sigma2), mean = mu."""

    def __init__(self, Sx: np.ndarray, Sx2: np.ndarray, N: np.ndarray):
        N = np.maximum(np.asarray(N, dtype=np.float64), 1)
        self._mu = np.asarray(Sx, dtype=np.float64) / N
        self._sigma2 = np.maximum(
            np.asarray(Sx2, dtype=np.float64) / N - self._mu ** 2,
            MIN_EPS,
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"mu": self._mu, "sigma2": self._sigma2}

    @property
    def mean(self) -> np.ndarray:
        return self._mu

    def overlap(self, other: MethylDistributionView) -> np.ndarray:
        if isinstance(other, NormalView):
            mu1, v1 = self._mu, self._sigma2
            mu2 = np.asarray(other.mean, dtype=np.float64)
            v2 = np.asarray(other.parameters["sigma2"], dtype=np.float64)
            n = min(len(mu1), len(mu2))
            mu1, v1, mu2, v2 = mu1[:n], v1[:n], mu2[:n], v2[:n]
            # Bhattacharyya for Normal: exp(- (mu1-mu2)^2 / (8 * (var1+var2)/2) ) = exp(- (mu1-mu2)^2 / (4*(v1+v2)))
            denom = 4 * (v1 + v2)
            denom = np.maximum(denom, MIN_EPS)
            bc = np.exp(-((mu1 - mu2) ** 2) / denom)
            return np.clip(bc, 0, 1)
        # Generic fallback: use difference in means
        om = np.asarray(other.mean, dtype=np.float64)
        n = min(len(self._mu), len(om))
        v2 = np.full(n, MIN_EPS)
        if "sigma2" in getattr(other, "parameters", {}):
            v2 = np.asarray(other.parameters["sigma2"], dtype=np.float64)[:n]
        denom = 4 * (self._sigma2[:n] + v2)
        denom = np.maximum(denom, MIN_EPS)
        bc = np.exp(-((self._mu[:n] - om[:n]) ** 2) / denom)
        return np.clip(bc, 0, 1)




# --- ECDF view (spline-interpolated from binned_stats) ---
# KS grid size for overlap
_ECDF_KS_GRID_SIZE = 256


class ECDFView:
    """
    Empirical CDF view: parameters from binned_stats (bin_edges, bin_counts).
    Uses monotone linear interpolation on bin edges (CPU and GPU share the same method).
    Mean = Sx/N, variance = sample variance from Sx, Sx2, N.
    """

    def __init__(
        self,
        bin_edges: np.ndarray,
        bin_counts: np.ndarray,
        Sx: np.ndarray,
        N: np.ndarray,
        Sx2: Optional[np.ndarray] = None,
    ):
        self._bin_edges = np.asarray(bin_edges, dtype=np.float64)
        self._bin_counts = np.asarray(bin_counts, dtype=np.float64)
        n_positions, n_bins = self._bin_counts.shape
        if len(self._bin_edges) != n_bins + 1:
            raise ValueError("bin_edges length must be n_bins + 1")
        self._Sx = np.asarray(Sx, dtype=np.float64)
        self._N = np.maximum(np.asarray(N, dtype=np.float64), 1.0)
        self._mean = self._Sx / self._N
        if Sx2 is not None:
            self._Sx2 = np.asarray(Sx2, dtype=np.float64)
            # Unbiased sample variance: (Sx2 - Sx²/N) / (N-1), distribution-independent
            self._variance = np.maximum(
                (self._Sx2 - (self._Sx ** 2) / self._N) / np.maximum(self._N - 1.0, 1.0),
                MIN_EPS,
            )
        else:
            self._Sx2 = None
            self._variance = np.full(n_positions, MIN_EPS, dtype=np.float64)

        # Build per-position CDF at bin edges: [0, cdf_1, cdf_2, ..., 1]
        total = np.sum(self._bin_counts, axis=1, keepdims=True)
        total = np.maximum(total, MIN_EPS)
        cdf_at_edges = np.cumsum(self._bin_counts, axis=1) / total
        cdf_at_edges = np.concatenate(
            [np.zeros((n_positions, 1), dtype=np.float64), cdf_at_edges],
            axis=1,
        )

        self._cdf_at_edges = cdf_at_edges
        self._n_positions = n_positions

    @property
    def bin_edges(self) -> np.ndarray:
        return self._bin_edges

    @property
    def bin_counts(self) -> np.ndarray:
        return self._bin_counts

    @property
    def parameters(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "bin_edges": self._bin_edges,
            "bin_counts": self._bin_counts,
            "Sx": self._Sx,
            "N": self._N,
        }
        if self._Sx2 is not None:
            out["Sx2"] = self._Sx2
        return out

    @property
    def mean(self) -> np.ndarray:
        return self._mean

    @property
    def variance(self) -> np.ndarray:
        return self._variance

    def _cdf(self, position_idx: int, x: np.ndarray) -> np.ndarray:
        from ..array_backend import get_array_module, cdf_linear_interp_batch

        x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
        xp, _ = get_array_module()
        return cdf_linear_interp_batch(
            xp,
            self._cdf_at_edges[position_idx : position_idx + 1],
            self._bin_edges,
            x,
        )[0]

    def _cdf_batch(
        self, position_indices: np.ndarray, grid: np.ndarray
    ) -> np.ndarray:
        """Evaluate CDF at multiple positions and grid points (linear interpolation)."""
        from ..array_backend import get_array_module, cdf_linear_interp_batch

        position_indices = np.asarray(position_indices, dtype=np.intp).ravel()
        grid = np.clip(np.asarray(grid, dtype=np.float64).ravel(), 0.0, 1.0)
        xp, _ = get_array_module()
        return cdf_linear_interp_batch(
            xp,
            self._cdf_at_edges[position_indices],
            self._bin_edges,
            grid,
        )

    def _pdf(self, position_idx: int, x: float) -> float:
        grid = np.linspace(0.0, 1.0, 256, dtype=np.float64)
        cdf_vals = self._cdf(position_idx, grid)
        idx = int(np.searchsorted(grid, x, side="right")) - 1
        idx = max(0, min(idx, len(grid) - 2))
        dx = max(float(grid[idx + 1] - grid[idx]), 1e-12)
        return max(float((cdf_vals[idx + 1] - cdf_vals[idx]) / dx), MIN_EPS)

    def _pdf_batch(
        self, position_indices: np.ndarray, grid: np.ndarray
    ) -> np.ndarray:
        """Evaluate piecewise PDF on interval midpoints; shape (P, len(grid)-1)."""
        position_indices = np.asarray(position_indices, dtype=np.intp).ravel()
        grid = np.clip(np.asarray(grid, dtype=np.float64).ravel(), 0.0, 1.0)
        cdf = self._cdf_batch(position_indices, grid)
        if grid.size < 2:
            return np.zeros((len(position_indices), 0), dtype=np.float64)
        dx = np.maximum(np.diff(grid), 1e-12)
        return np.maximum(np.diff(cdf, axis=1), 0.0) / dx[np.newaxis, :]

    @staticmethod
    def _pdf_integration_grid(grid: np.ndarray) -> np.ndarray:
        """Abscissa matching ``_pdf_batch`` output (interval midpoints)."""
        grid = np.clip(np.asarray(grid, dtype=np.float64).ravel(), 0.0, 1.0)
        if grid.size < 2:
            return grid
        return (grid[:-1] + grid[1:]) / 2.0

    def overlap(self, other: MethylDistributionView) -> np.ndarray:
        if isinstance(other, ECDFView):
            n = min(self._n_positions, len(other.mean))
            grid = np.linspace(0.0, 1.0, _ECDF_KS_GRID_SIZE, dtype=np.float64)
            pdf_grid = self._pdf_integration_grid(grid)
            idx = np.arange(n, dtype=np.intp)
            pdf1 = self._pdf_batch(idx, grid)
            pdf2 = other._pdf_batch(idx, grid)
            trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")
            area1 = trapz(pdf1, pdf_grid, axis=1)
            area2 = trapz(pdf2, pdf_grid, axis=1)
            pdf1 = pdf1 / np.maximum(area1[:, None], MIN_EPS)
            pdf2 = pdf2 / np.maximum(area2[:, None], MIN_EPS)
            overlap = trapz(np.minimum(pdf1, pdf2), pdf_grid, axis=1)
            return np.clip(overlap, 0.0, 1.0)
        om = np.asarray(other.mean, dtype=np.float64)
        n = min(len(self._mean), len(om))
        return np.clip(1.0 - np.abs(self._mean[:n] - om[:n]), 0.0, 1.0)



def get_distribution_view(
    centroid: Union[MethylCentroid, MethylSample],
    mode: str,
    positions: Optional[np.ndarray] = None,
) -> MethylDistributionView:
    """Build a distribution view from a centroid (ecdf mode only)."""
    if mode != "ecdf":
        raise ValueError(f"Only 'ecdf' mode is supported, got '{mode}'.")
    if positions is not None:
        pos_arr = np.asarray(centroid.pos.values, dtype=np.uint32)
        idx = np.isin(pos_arr, np.asarray(positions, dtype=np.uint32))
        centroid = centroid[idx]

        binned = centroid.binned_stats
        if binned is None or "bin_edges" not in binned or "bin_counts" not in binned:
            raise ValueError(
                "ecdf view requires centroid with binned_stats (bin_edges, bin_counts). "
                "Build centroid with binned_stats_bins (default 20)."
            )
        bin_edges = np.asarray(binned["bin_edges"], dtype=np.float64)
        bin_counts = np.asarray(binned["bin_counts"], dtype=np.float64)
        Sx = np.asarray(centroid.Sx.values, dtype=np.float64)
        N = np.asarray(centroid.N.values, dtype=np.float64)
        Sx2 = np.asarray(centroid.Sx2.values, dtype=np.float64) if centroid.Sx2 is not None else None
        return ECDFView(bin_edges, bin_counts, Sx, N, Sx2)

def log_probability_sample_given_centroid(
    sample: Any,
    centroid: Union[MethylCentroid, MethylSample],
    mode: str,
    positions: Optional[np.ndarray] = None,
    use_gpu: bool = False,
) -> np.ndarray:
    """
    Log P(sample | centroid) per position for the given mode.
    Aligns sample to centroid on common positions; returns log prob per position (0/1 safe).
    """
    pos_c = np.asarray(centroid.pos.values, dtype=np.uint32)
    pos_s = np.asarray(sample.pos.values, dtype=np.uint32)
    if positions is not None:
        positions = np.asarray(positions, dtype=np.uint32)
        pos_c = pos_c[np.isin(pos_c, positions)]
    common = np.intersect1d(pos_c, pos_s, assume_unique=True)
    if len(common) == 0:
        return np.array([], dtype=np.float64)
    idx_c = np.searchsorted(pos_c, common)
    idx_s = np.searchsorted(pos_s, common)
    n = len(common)

    binned = centroid.binned_stats
    if binned is None or "bin_edges" not in binned or "bin_counts" not in binned:
        raise ValueError(
            "log_probability with mode=ecdf requires centroid with binned_stats. "
            "Build centroid with binned_stats_bins (default 20)."
        )
    view = get_distribution_view(centroid, "ecdf", positions=common)
    cov_s = np.asarray(sample.mC.values, dtype=np.float64)[idx_s] + np.asarray(sample.uC.values, dtype=np.float64)[idx_s]
    x = np.where(cov_s > 0, np.asarray(sample.mC.values, dtype=np.float64)[idx_s] / cov_s, 0.5)
    x = _clip_proportion(x)
    log_p = np.zeros(n, dtype=np.float64)
    for i in range(n):
        pdf_val = view._pdf(i, float(x[i]))
        log_p[i] = np.log(max(pdf_val, MIN_EPS))
    return log_p

def overlap_between_centroids(
    centroid1: Union[MethylCentroid, MethylSample],
    centroid2: Union[MethylCentroid, MethylSample],
    mode: str,
    positions: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Overlap between two centroids for the given mode (Bhattacharyya or mode-specific)."""
    v1 = get_distribution_view(centroid1, mode, positions)
    v2 = get_distribution_view(centroid2, mode, positions)
    return v1.overlap(v2)
