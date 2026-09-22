"""
Compare continuous ECDF (per position) to theoretical CDFs (Normal, Beta, Beta-Binomial)
via KS statistic and significance tests. Used to identify the closest theoretical
distribution and whether it could be used instead of ECDF.

Note: This module uses CPU-only scipy.stats (truncnorm, beta, kstwobign). A GPU path
would require vectorizing over positions: batch-evaluate theoretical CDFs on a (N, grid_size)
grid using cupyx.scipy.stats (where available), batch-evaluate ECDF per position (e.g. from
binned CDF or batched PCHIP), then KS = max(|F_ecdf - F_theory|) and argmin per position on GPU.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import numpy as np

try:
    from scipy.stats import beta as beta_dist
    from scipy.stats import truncnorm
    from scipy.stats import kstwobign
except ImportError:
    beta_dist = None
    truncnorm = None
    kstwobign = None

MIN_EPS = 1e-12


def _cdf_normal_truncated(
    x: np.ndarray, mu: float, sigma2: float
) -> np.ndarray:
    """Truncated Normal CDF on [0,1] with mean mu and variance sigma2."""
    if sigma2 <= 0 or not np.isfinite(sigma2):
        return np.where(np.asarray(x, dtype=np.float64) >= mu, 1.0, 0.0)
    sigma = np.sqrt(max(sigma2, MIN_EPS))
    a_std = (0.0 - mu) / sigma
    b_std = (1.0 - mu) / sigma
    if a_std >= b_std:
        return np.where(np.asarray(x, dtype=np.float64) >= mu, 1.0, 0.0)
    x_arr = np.asarray(x, dtype=np.float64)
    return truncnorm.cdf(x_arr, a_std, b_std, loc=mu, scale=sigma)


def _cdf_beta(x: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    """Beta CDF on [0,1]."""
    if alpha <= 0 or beta <= 0 or not (np.isfinite(alpha) and np.isfinite(beta)):
        return np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    return beta_dist.cdf(np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0), alpha, beta)


def ecdf_vs_theoretical_ks(
    ecdf_view: Any,
    position_idx: int,
    mu: float,
    sigma2: float,
    alpha: float,
    beta: float,
    alpha_bb: Optional[float] = None,
    beta_bb: Optional[float] = None,
    grid_size: int = 256,
) -> Dict[str, Any]:
    """
    Compute KS statistics between the continuous ECDF at a position and the
    theoretical CDFs (Normal, Beta, Beta-Binomial proportion).

    The grid is chosen fine enough (default 256) so the discrete maximum
    approximates the true sup-norm over [0,1].

    Returns:
        Dict with ks_normal, ks_beta, ks_betabinomial (np.nan when skipped),
        and closest (name of distribution with smallest KS).
    """
    grid = np.linspace(0.0, 1.0, grid_size, dtype=np.float64)
    f_ecdf = ecdf_view._cdf(position_idx, grid)

    ks_normal = np.nan
    if sigma2 > 0 and np.isfinite(sigma2):
        f_norm = _cdf_normal_truncated(grid, mu, sigma2)
        ks_normal = float(np.max(np.abs(f_ecdf - f_norm)))

    ks_beta = float(np.max(np.abs(f_ecdf - _cdf_beta(grid, alpha, beta))))

    ks_betabinomial = np.nan
    if alpha_bb is not None and beta_bb is not None and alpha_bb > 0 and beta_bb > 0:
        if np.isfinite(alpha_bb) and np.isfinite(beta_bb):
            f_bb = _cdf_beta(grid, alpha_bb, beta_bb)
            ks_betabinomial = float(np.max(np.abs(f_ecdf - f_bb)))

    candidates: List[tuple] = [("beta", ks_beta)]
    if np.isfinite(ks_normal):
        candidates.append(("normal", ks_normal))
    if np.isfinite(ks_betabinomial):
        candidates.append(("betabinomial", ks_betabinomial))
    closest = min(candidates, key=lambda t: t[1])[0]

    return {
        "ks_normal": ks_normal,
        "ks_beta": ks_beta,
        "ks_betabinomial": ks_betabinomial,
        "closest": closest,
    }


def ecdf_vs_theoretical_ks_pvalue(
    ecdf_view: Any,
    position_idx: int,
    mu: float,
    sigma2: float,
    alpha: float,
    beta: float,
    n_samples: int,
    alpha_bb: Optional[float] = None,
    beta_bb: Optional[float] = None,
    grid_size: int = 256,
    alpha_threshold: float = 0.05,
) -> Dict[str, Any]:
    """
    Same as ecdf_vs_theoretical_ks plus asymptotic p-values for each theoretical
    (H0: data from that distribution).

    P-value interpretation vs the ECDF:
    - Small p (e.g. < 0.05): reject H0 → that theoretical distribution is inconsistent with the ECDF (different from it).
    - Large p (e.g. > 0.05): do not reject → that theoretical distribution could match the ECDF (consistent with it).
    could_use_instead is True when the closest distribution has p > alpha_threshold.

    When the mean is near 0 or 1 or variance is very small, the asymptotic KS approximation can be less reliable.
    """
    out = ecdf_vs_theoretical_ks(
        ecdf_view, position_idx, mu, sigma2, alpha, beta, alpha_bb, beta_bb, grid_size
    )
    n = max(int(n_samples), 1)
    sqrt_n = np.sqrt(n)

    def p_from_ks(ks: float) -> float:
        if not np.isfinite(ks) or ks < 0:
            return np.nan
        # Asymptotic two-sided KS: sqrt(n)*D_n has kstwobign limit; p = P(stat >= observed) = sf(sqrt_n * ks)
        p = float(kstwobign.sf(sqrt_n * ks))
        return float(np.clip(p, 0.0, 1.0))

    out["p_normal"] = p_from_ks(out["ks_normal"])
    out["p_beta"] = p_from_ks(out["ks_beta"])
    out["p_betabinomial"] = p_from_ks(out["ks_betabinomial"])

    closest = out["closest"]
    p_closest = {
        "normal": out["p_normal"],
        "beta": out["p_beta"],
        "betabinomial": out["p_betabinomial"],
    }.get(closest, np.nan)
    out["could_use_instead"] = bool(np.isfinite(p_closest) and p_closest > alpha_threshold)
    return out


def compare_ecdf_to_theoretical_at_positions(
    centroid: Any,
    position_indices: Optional[np.ndarray] = None,
    grid_size: int = 256,
    include_pvalues: bool = True,
) -> List[Dict[str, Any]]:
    """
    For each position (or position_indices), compare ECDF to Normal, Beta, and
    Beta-Binomial; return a list of dicts with KS stats, closest, and optionally p-values.

    Centroid must have binned_stats and sufficient stats (Sx, Sx2, N, alpha, beta;
    alpha_bb, beta_bb if available).
    """
    binned = getattr(centroid, "binned_stats", None)
    if binned is None or "bin_edges" not in binned or "bin_counts" not in binned:
        raise ValueError(
            "compare_ecdf_to_theoretical_at_positions requires centroid with binned_stats."
        )
    from .core.distribution_views import ECDFView

    bin_edges = np.asarray(binned["bin_edges"], dtype=np.float64)
    bin_counts = np.asarray(binned["bin_counts"], dtype=np.float64)
    Sx = np.asarray(centroid.Sx.values, dtype=np.float64)
    N = np.asarray(centroid.N.values, dtype=np.float64)
    Sx2 = np.asarray(centroid.Sx2.values, dtype=np.float64) if hasattr(centroid, "Sx2") else None
    ecdf_view = ECDFView(bin_edges, bin_counts, Sx, N, Sx2)

    n_pos = ecdf_view._n_positions
    indices = (
        np.asarray(position_indices, dtype=np.intp)
        if position_indices is not None
        else np.arange(n_pos)
    )
    indices = indices[(indices >= 0) & (indices < n_pos)]

    # Use centroid.alpha, centroid.beta (MoM from N, Sx, Sx2) for theoretical CDF comparison
    alpha_bb_arr = np.asarray(centroid.alpha.values, dtype=np.float64)
    beta_bb_arr = np.asarray(centroid.beta.values, dtype=np.float64)

    mu_arr = np.asarray(centroid.Sx.values, dtype=np.float64) / np.maximum(
        np.asarray(centroid.N.values, dtype=np.float64), 1.0
    )
    sigma2_arr = np.full(n_pos, MIN_EPS, dtype=np.float64)
    if hasattr(centroid, "Sx2") and Sx2 is not None:
        # Unbiased sample variance: (Sx2 - Sx²/N) / (N-1), distribution-independent
        sigma2_arr = np.maximum(
            (Sx2 - (Sx ** 2) / np.maximum(N, 1.0)) / np.maximum(N - 1.0, 1.0),
            MIN_EPS,
        )
    alpha_arr = np.asarray(centroid.alpha.values, dtype=np.float64)
    beta_arr = np.asarray(centroid.beta.values, dtype=np.float64)
    pos_arr = np.asarray(centroid.pos.values, dtype=np.uint32) if hasattr(centroid, "pos") else np.arange(n_pos, dtype=np.uint32)

    results: List[Dict[str, Any]] = []
    for idx in indices:
        i = int(idx)
        mu = float(mu_arr[i])
        sigma2 = float(sigma2_arr[i]) if np.isfinite(sigma2_arr[i]) else MIN_EPS
        a = float(alpha_arr[i])
        b = float(beta_arr[i])
        a_bb = float(alpha_bb_arr[i]) if alpha_bb_arr is not None else None
        b_bb = float(beta_bb_arr[i]) if beta_bb_arr is not None else None
        n_samp = int(N[i])

        if include_pvalues:
            row = ecdf_vs_theoretical_ks_pvalue(
                ecdf_view, i, mu, sigma2, a, b, n_samp,
                alpha_bb=a_bb, beta_bb=b_bb, grid_size=grid_size,
            )
        else:
            row = ecdf_vs_theoretical_ks(
                ecdf_view, i, mu, sigma2, a, b,
                alpha_bb=a_bb, beta_bb=b_bb, grid_size=grid_size,
            )
        row["position"] = int(pos_arr[i]) if i < len(pos_arr) else i
        row["position_index"] = i
        results.append(row)
    return results


__all__ = [
    "ecdf_vs_theoretical_ks",
    "ecdf_vs_theoretical_ks_pvalue",
    "compare_ecdf_to_theoretical_at_positions",
]
