"""
Statistical test functions for methylation analysis.

This module provides functions for statistical testing, FDR correction,
and meta-analysis commonly used in methylation studies.
"""

import logging
import numpy as np
from typing import Tuple, Optional, Any, Dict

# Import GPU detection utilities
from .gpu_detection import is_gpu_available, is_cupyx_scipy_stats_available, get_cupy
from .metrics_core import DistanceCalculator

# Optional plotting imports
try:
    import plotly.graph_objects as go
    import plotly.io as pio
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

logger = logging.getLogger(__name__)


def storey_qvalues(
    p_array: np.ndarray,
    lambdas: Optional[np.ndarray] = None,
    plot_pi0: bool = False,
    plot_path: str = "pi0_vs_lambda.html",
    plot_title: str = "Storey's Pi0 vs Lambda"
) -> Tuple[np.ndarray, float]:
    """
    Compute Storey's q-values for FDR correction.

    This function estimates the proportion of true null hypotheses (π₀) and
    computes q-values, which represent the expected proportion of false
    discoveries among all discoveries up to and including the current test.

    Args:
        p_array: Array of p-values
        lambdas: Array of lambda values for π₀ estimation (default: linspace(0.01, 0.95, 100))
        plot_pi0: Whether to create a plot of π₀ vs λ
        plot_path: Path for the π₀ plot (if plotting is enabled)
        plot_title: Title for the π₀ plot

    Returns:
        Tuple of (q_values, pi0_estimate)

    Raises:
        ValueError: If input validation fails
    """
    from .metrics_core import DistanceCalculator

    # Input validation
    if p_array.size == 0:
        raise ValueError("Input p-values array cannot be empty")

    if np.any((p_array < 0) | (p_array > 1)):
        raise ValueError("All p-values must be in range [0, 1]")

    # Check for GPU availability for stats functions
    gpu_available = is_gpu_available() and is_cupyx_scipy_stats_available()
    calc = None
    xp = np
    p_work = np.asarray(p_array, dtype=np.float64)
    lambdas_work = np.asarray(lambdas, dtype=np.float64) if lambdas is not None else None

    if gpu_available:
        try:
            calc = DistanceCalculator()
            xp = calc.cp
            p_work = calc.cp.asarray(p_work, dtype=calc.cp.float32)
            if lambdas_work is not None:
                lambdas_work = calc.cp.asarray(lambdas_work, dtype=calc.cp.float32)
        except Exception as e:
            logger.warning("storey_qvalues GPU setup failed; falling back to CPU: %s", e)
            gpu_available = False
            calc = None
            xp = np
            p_work = np.asarray(p_array, dtype=np.float64)
            lambdas_work = np.asarray(lambdas, dtype=np.float64) if lambdas is not None else None

    try:
        m = len(p_work)
        lambdas_work = lambdas_work if lambdas_work is not None else xp.linspace(0.01, 0.95, 100)
        pi0s = xp.asarray([(xp.sum(p_work > lambda_val) / ((1 - lambda_val) * m)) for lambda_val in lambdas_work])
        pi0 = min(float(xp.median(pi0s)), 1.0)
    except Exception as e:
        if not gpu_available:
            raise
        logger.warning("storey_qvalues GPU execution failed; falling back to CPU: %s", e)
        gpu_available = False
        calc = None
        xp = np
        p_work = np.asarray(p_array, dtype=np.float64)
        lambdas_work = np.asarray(lambdas, dtype=np.float64) if lambdas is not None else None
        m = len(p_work)
        lambdas_work = lambdas_work if lambdas_work is not None else np.linspace(0.01, 0.95, 100)
        pi0s = np.asarray([(np.sum(p_work > lambda_val) / ((1 - lambda_val) * m)) for lambda_val in lambdas_work])
        pi0 = min(float(np.median(pi0s)), 1.0)

    if plot_pi0 and PLOTLY_AVAILABLE:
        try:
            import signal

            # Set up a timeout for plotting to prevent hangs
            def timeout_handler(signum, frame):
                raise TimeoutError("Plotting timed out")

            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(10)  # 10 second timeout

            try:
                lambdas_cpu = (
                    calc.cp.asnumpy(lambdas_work) if (gpu_available and calc is not None) else np.asarray(lambdas_work)
                )
                pi0s_cpu = calc.cp.asnumpy(pi0s) if (gpu_available and calc is not None) else np.asarray(pi0s)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=lambdas_cpu, y=pi0s_cpu, mode='lines+markers', name='pi0(lambda)'))
                fig.add_hline(y=pi0, line=dict(color='red', dash='dash'), name='min pi0')
                fig.update_layout(
                    title=plot_title,
                    xaxis_title="Lambda",
                    yaxis_title="pi0 estimate",
                    title_x=0.5,  # Center the title
                    showlegend=True
                )
                pio.write_html(fig, file=plot_path, auto_open=False)
            finally:
                signal.alarm(0)  # Cancel the timeout

        except (TimeoutError, Exception) as e:
            logger.warning(f"FDR plotting failed ({e}), continuing without plot")
    elif plot_pi0 and not PLOTLY_AVAILABLE:
        logger.warning("Plotly not available for π₀ plotting")

    p_sorted = xp.sort(p_work)
    order = xp.argsort(p_work)
    qvals = pi0 * m * p_sorted / (xp.arange(1, m + 1))
    # minimum.accumulate is not implemented in CuPy; use NumPy for this step
    if gpu_available and calc is not None:
        qvals_np = calc.cp.asnumpy(qvals)
    else:
        qvals_np = np.asarray(qvals)
    qvals = np.minimum.accumulate(qvals_np[::-1])[::-1]
    if gpu_available and calc is not None:
        qvals = calc.cp.asarray(qvals, dtype=calc.cp.float32)

    q_final = xp.empty_like(qvals)
    q_final[order] = qvals

    # Ensure output is CPU array
    if gpu_available and calc is not None:
        return calc.cp.asnumpy(q_final), pi0
    else:
        return q_final, pi0


def stouffer_global_p(p_array: np.ndarray, weights: Optional[np.ndarray] = None) -> Tuple[float, float]:
    """
    Combine p-values using Stouffer's method.

    This function combines multiple p-values into a single global p-value using
    Stouffer's Z-score method, optionally with weights.

    Args:
        p_array: Array of p-values to combine
        weights: Optional array of weights for each p-value

    Returns:
        Tuple of (combined_p_value, z_score)

    Raises:
        ValueError: If input validation fails
    """
    # Input validation
    if p_array.size == 0:
        raise ValueError("Input p-values array cannot be empty")

    if np.any((p_array < 0) | (p_array > 1)):
        raise ValueError("All p-values must be in range [0, 1]")

    if weights is not None:
        if weights.shape != p_array.shape:
            raise ValueError(f"Weights shape {weights.shape} must match p-values shape {p_array.shape}")
        if np.any(weights < 0):
            raise ValueError("All weights must be non-negative")

    # Handle extreme values (0 and 1) that cause infinite z-scores
    p_array_clean = np.clip(p_array, 1e-10, 1.0 - 1e-10)

    # Check for GPU availability for stats functions
    gpu_available = is_gpu_available() and is_cupyx_scipy_stats_available()

    if gpu_available:
        try:
            import cupyx.scipy.stats as cpstats
            from .metrics_core import DistanceCalculator
            calc = DistanceCalculator()

            p_gpu = calc.cp.asarray(p_array_clean, dtype=calc.cp.float32)
            z_scores = cpstats.norm.isf(p_gpu)
            if weights is not None:
                w_gpu = calc.cp.asarray(weights, dtype=calc.cp.float32)
                z = calc.cp.sum(w_gpu * z_scores) / calc.cp.sqrt(calc.cp.sum(w_gpu ** 2))
            else:
                z = calc.cp.sum(z_scores) / calc.cp.sqrt(len(z_scores))

            # Handle extreme z-scores to avoid numerical issues
            z_val = float(z)
            if z_val > 10 or z_val < -10:
                return 0.0, z_val  # Very significant
            else:
                return float(cpstats.norm.cdf(z_val)), z_val
        except ImportError:
            gpu_available = False

    if not gpu_available:
        from scipy.stats import norm

        z_scores = norm.isf(p_array_clean)
        if weights is not None:
            z = np.sum(weights * z_scores) / np.sqrt(np.sum(weights ** 2))
        else:
            z = np.sum(z_scores) / np.sqrt(len(z_scores))

        # Handle extreme z-scores to avoid numerical issues
        if z > 10:
            return 0.0, z  # Very significant
        elif z < -10:
            return 0.0, z  # Very significant (large negative z-score indicates strong evidence)
        else:
            return norm.cdf(z), z


# SciPy functions are accessed through DistanceCalculator when needed













def aggregate_pvalues_fisher(pvalues: np.ndarray, weights: np.ndarray = None) -> float:
    """
    Fisher's method for combining independent p-values.

    Combines p-values using the chi-squared distribution with 2k degrees of freedom,
    where k is the number of p-values.

    Args:
        pvalues: Array of p-values to combine
        weights: Optional weights for each p-value (must sum to 1)

    Returns:
        Combined p-value

    Notes:
        - Assumes independence between tests
        - Sensitive to small p-values
        - More powerful than Stouffer's for detecting consistent effects
    """
    if len(pvalues) == 0:
        return 1.0

    # Convert p-values to chi-squared statistics
    if weights is None:
        # Unweighted: sum of -2*log(p_i)
        chi2_stat = -2 * np.sum(np.log(pvalues))
    else:
        # Weighted: sum of weights * -2*log(p_i)
        if len(weights) != len(pvalues) or not np.isclose(np.sum(weights), 1.0):
            raise ValueError("Weights must be same length as pvalues and sum to 1")
        chi2_stat = -2 * np.sum(weights * np.log(pvalues))

    # Degrees of freedom = 2 * number of tests
    df = 2 * len(pvalues)

    # Combined p-value from chi-squared distribution
    from scipy.stats import chi2
    combined_p = 1 - chi2.cdf(chi2_stat, df)

    return max(combined_p, 0.0)  # Ensure non-negative


def aggregate_pvalues_stouffer(pvalues: np.ndarray, weights: np.ndarray = None) -> float:
    """
    Stouffer's method for combining independent p-values.

    Converts p-values to z-scores and combines them using weighted average.
    More robust than Fisher's method when effects are not extremely significant.

    Args:
        pvalues: Array of p-values to combine
        weights: Optional weights for each p-value (must sum to 1)

    Returns:
        Combined p-value

    Notes:
        - Assumes independence between tests
        - More robust than Fisher's for moderate effects
        - Less sensitive to single very small p-values
    """
    if len(pvalues) == 0:
        return 1.0

    # Convert p-values to z-scores (one-tailed)
    from scipy.stats import norm
    z_scores = norm.ppf(1 - pvalues)  # Convert to z-scores

    # Handle edge cases
    z_scores = np.clip(z_scores, -8.0, 8.0)  # Prevent extreme values

    if weights is None:
        # Unweighted average
        combined_z = np.mean(z_scores)
    else:
        # Weighted average
        if len(weights) != len(pvalues) or not np.isclose(np.sum(weights), 1.0):
            raise ValueError("Weights must be same length as pvalues and sum to 1")
        combined_z = np.sum(weights * z_scores)

    # Convert back to p-value (one-tailed)
    combined_p = 1 - norm.cdf(combined_z)

    return max(combined_p, 0.0)


def aggregate_pvalues_lancaster(pvalues: np.ndarray, weights: np.ndarray = None,
                               degrees: np.ndarray = None) -> float:
    """
    Lancaster's method - generalization of Fisher's method.

    Allows different degrees of freedom for each test, making it suitable
    for combining p-values from different types of statistical tests.

    Args:
        pvalues: Array of p-values to combine
        weights: Optional weights for each p-value
        degrees: Degrees of freedom for each test (default: 1 for each)

    Returns:
        Combined p-value

    Notes:
        - Generalization of Fisher's method
        - Accounts for different test types/degrees of freedom
        - More flexible than basic Fisher's method
    """
    if len(pvalues) == 0:
        return 1.0

    if degrees is None:
        degrees = np.ones(len(pvalues))

    # Convert to chi-squared statistics
    chi2_stats = -2 * np.log(pvalues) / degrees

    if weights is None:
        combined_chi2 = np.sum(chi2_stats)
        total_df = 2 * np.sum(degrees)
    else:
        if len(weights) != len(pvalues) or not np.isclose(np.sum(weights), 1.0):
            raise ValueError("Weights must be same length as pvalues and sum to 1")
        combined_chi2 = np.sum(weights * chi2_stats)
        total_df = 2 * np.sum(weights * degrees)

    from scipy.stats import chi2
    combined_p = 1 - chi2.cdf(combined_chi2, total_df)

    return max(combined_p, 0.0)


def aggregate_pvalues_tippett(pvalues: np.ndarray) -> float:
    """
    Tippett's method - minimum p-value approach.

    Uses the minimum p-value to test against multiplicity.
    Equivalent to testing if all null hypotheses are true.

    Args:
        pvalues: Array of p-values to combine

    Returns:
        Combined p-value

    Notes:
        - Conservative approach
        - Good for detecting if any effect exists
        - Less powerful than other methods for consistent effects
    """
    if len(pvalues) == 0:
        return 1.0

    min_p = np.min(pvalues)
    k = len(pvalues)

    # Combined p-value = 1 - (1 - min_p)^k
    combined_p = 1 - (1 - min_p)**k

    return max(combined_p, 0.0)


def aggregate_pvalues_edgington(pvalues: np.ndarray) -> float:
    """
    Edgington's method - sum of p-values.

    Simple method that sums p-values and compares to a uniform distribution.
    Good for detecting overall significance when effects are weak.

    Args:
        pvalues: Array of p-values to combine

    Returns:
        Combined p-value

    Notes:
        - Simple and intuitive
        - Good for detecting weak but consistent effects
        - Less sensitive than Fisher/Stouffer for strong effects
    """
    if len(pvalues) == 0:
        return 1.0

    k = len(pvalues)
    sum_p = np.sum(pvalues)

    # Under null hypothesis, sum follows Irwin-Hall distribution
    # For large k, approximate with normal distribution
    if k < 20:
        # For small k, use beta distribution approximation
        # Sum of uniforms is Irwin-Hall, but for p-values approximation:
        from scipy.stats import beta
        # P(Sum >= s) where Sum ~ Irwin-Hall(k)
        # Approximation using beta distribution
        combined_p = 1 - beta.cdf(sum_p, k, 1)
    else:
        # Normal approximation for large k
        mean = k / 2
        std = np.sqrt(k / 12)
        from scipy.stats import norm
        combined_p = 1 - norm.cdf(sum_p, mean, std)

    return max(combined_p, 0.0)


def aggregate_pvalues_mudholkar_george(pvalues: np.ndarray) -> float:
    """
    Mudholkar-George method for combining p-values.

    Uses a transformation that stabilizes variance and provides good power.

    Args:
        pvalues: Array of p-values to combine

    Returns:
        Combined p-value

    Notes:
        - Good balance of power and robustness
        - Performs well across different scenarios
        - Less sensitive to outliers than Fisher
    """
    if len(pvalues) == 0:
        return 1.0

    k = len(pvalues)

    # Mudholkar-George transformation: z_i = Φ^{-1}(1 - p_i^{1/k})
    from scipy.stats import norm
    transformed = norm.ppf(1 - pvalues**(1/k))

    # Combine using Stouffer-like approach
    combined_z = np.sum(transformed) / np.sqrt(k)

    # Convert back to p-value
    combined_p = 1 - norm.cdf(combined_z)

    return max(combined_p, 0.0)


def aggregate_pvalues_simes(pvalues: np.ndarray) -> float:
    """
    Simes method for combining p-values (Generalized Simes).

    Based on Simes inequality, combines p-values by taking the minimum
    of k×p_{(i)}/i for i=1 to k, where p_{(i)} is the i-th smallest p-value.

    This is equivalent to the Bonferroni-Holm procedure but used for combination
    rather than correction. Controls FWER under positive dependence.

    Args:
        pvalues: Array of p-values to combine

    Returns:
        Combined p-value

    Notes:
        - Conservative approach based on Simes inequality
        - Good for detecting presence of ANY significant effect
        - More powerful than Bonferroni for combination tasks
        - Controls Type I error under positive dependence
    """
    if len(pvalues) == 0:
        return 1.0

    k = len(pvalues)
    sorted_p = np.sort(pvalues)

    # Simes combined p-value: min over i of (k × p_{(i)} / i)
    # This is the same as the Holm-Bonferroni procedure
    simes_values = [min(1.0, k * sorted_p[i] / (i + 1)) for i in range(k)]
    combined_p = min(simes_values)

    return combined_p


def ecdf_ks_statistic(
    ecdf_view1: Any,
    ecdf_view2: Any,
    position_indices: np.ndarray,
    grid_size: int = 256,
) -> np.ndarray:
    """
    KS statistic between two ECDFs at each position: D = sup_x |F1(x) - F2(x)| on a grid in [0, 1].
    Uses vectorized _cdf_batch when available for speed; falls back to per-position _cdf otherwise.
    """
    position_indices = np.asarray(position_indices, dtype=np.intp).ravel()
    grid = np.linspace(0.0, 1.0, grid_size, dtype=np.float64)
    use_batch = (
        hasattr(ecdf_view1, "_cdf_batch")
        and hasattr(ecdf_view2, "_cdf_batch")
    )
    if use_batch:
        f1 = ecdf_view1._cdf_batch(position_indices, grid)  # (n_positions, grid_size)
        f2 = ecdf_view2._cdf_batch(position_indices, grid)
        ks_stats = np.max(np.abs(f1 - f2), axis=1)
        return ks_stats.astype(np.float64)
    ks_stats = np.zeros(len(position_indices), dtype=np.float64)
    for i, pos_idx in enumerate(position_indices):
        pos_idx = int(pos_idx)
        f1 = ecdf_view1._cdf(pos_idx, grid)
        f2 = ecdf_view2._cdf(pos_idx, grid)
        ks_stats[i] = np.max(np.abs(f1 - f2))
    return ks_stats


def ecdf_ks_pvalue(
    ecdf_view1: "ECDFView",
    ecdf_view2: "ECDFView",
    position_indices: np.ndarray,
    n1: np.ndarray,
    n2: np.ndarray,
    grid_size: int = 256,
    prefer_gpu: Optional[bool] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    KS statistic and asymptotic two-sided p-value for two ECDFs at each position.
    n_eff = harmonic mean of n1, n2; p = kstwobign.sf(sqrt(n_eff) * D).
    """
    from .array_backend import kolmogorov_sf

    ks_stats = ecdf_ks_statistic(ecdf_view1, ecdf_view2, position_indices, grid_size)
    n1 = np.asarray(n1, dtype=np.float64).ravel()
    n2 = np.asarray(n2, dtype=np.float64).ravel()
    n_eff = 2.0 / (1.0 / np.maximum(n1, 1) + 1.0 / np.maximum(n2, 1))
    sqrt_n_eff = np.sqrt(n_eff)
    p_values = kolmogorov_sf(sqrt_n_eff * ks_stats, prefer_gpu=prefer_gpu)
    return ks_stats, p_values


def mann_whitney_from_bin_counts(
    bc1: np.ndarray,
    bc2: np.ndarray,
    n1: np.ndarray,
    n2: np.ndarray,
    prefer_gpu: Optional[bool] = None,
) -> Dict[str, np.ndarray]:
    """
    Vectorized Mann-Whitney U test using centroid bin-count histograms.

    The histogram approximation counts all pairs where a group-1 sample falls in a
    strictly larger bin than a group-2 sample, plus half credit for tied bins.
    """
    from .array_backend import get_array_module, norm_sf, to_cpu

    xp, _ = get_array_module(prefer_gpu)
    bc1 = xp.asarray(bc1, dtype=xp.float64)
    bc2 = xp.asarray(bc2, dtype=xp.float64)
    if bc1.ndim == 1:
        bc1 = bc1.reshape(1, -1)
        bc2 = bc2.reshape(1, -1)

    n1 = xp.asarray(n1, dtype=xp.float64).ravel()
    n2 = xp.asarray(n2, dtype=xp.float64).ravel()
    if int(n1.size) != bc1.shape[0]:
        n1 = xp.resize(n1, bc1.shape[0])
    if int(n2.size) != bc2.shape[0]:
        n2 = xp.resize(n2, bc2.shape[0])

    cs2 = xp.cumsum(bc2, axis=1)
    lower_than_bin = xp.concatenate(
        [xp.zeros((bc2.shape[0], 1), dtype=xp.float64), cs2[:, :-1]],
        axis=1,
    )
    u_stat = xp.sum(bc1 * lower_than_bin, axis=1) + 0.5 * xp.sum(bc1 * bc2, axis=1)

    total_n = xp.maximum(n1 + n2, 0.0)
    ties = bc1 + bc2
    denom = xp.maximum(total_n * xp.maximum(total_n - 1.0, 0.0), 1.0)
    tie_corr = xp.sum(ties * (ties**2 - 1.0), axis=1) / denom
    var_u = (n1 * n2 / 12.0) * xp.maximum((total_n + 1.0) - tie_corr, 0.0)

    mean_u = (n1 * n2) / 2.0
    valid = (n1 > 0) & (n2 > 0) & xp.isfinite(var_u) & (var_u > 0.0)
    z_stat = xp.zeros_like(u_stat, dtype=xp.float64)
    z_stat[valid] = (u_stat[valid] - mean_u[valid]) / xp.sqrt(var_u[valid])
    p_value = 2.0 * xp.asarray(
        norm_sf(to_cpu(xp.abs(z_stat)), prefer_gpu=prefer_gpu), dtype=xp.float64
    )
    p_value = xp.where(valid, p_value, 1.0)
    p_value = xp.clip(p_value, 1e-300, 1.0)

    return {
        "u_stat": to_cpu(u_stat).astype(np.float64),
        "z_stat": to_cpu(z_stat).astype(np.float64),
        "p_value": to_cpu(p_value).astype(np.float64),
        "var_u": to_cpu(var_u).astype(np.float64),
    }


def dl_heterogeneity(
    Sm: np.ndarray,
    Su: np.ndarray,
    Swx2: np.ndarray,
    Sc2: np.ndarray,
    N: np.ndarray,
) -> Dict[str, np.ndarray]:
    """
    DerSimonian-Laird-style decomposition of between-sample heterogeneity using
    centroid sufficient statistics already stored in MethylCentroid.
    """
    Sm = np.asarray(Sm, dtype=np.float64).ravel()
    Su = np.asarray(Su, dtype=np.float64).ravel()
    Swx2 = np.asarray(Swx2, dtype=np.float64).ravel()
    Sc2 = np.asarray(Sc2, dtype=np.float64).ravel()
    N = np.asarray(N, dtype=np.float64).ravel()

    c_total = np.maximum(Sm + Su, 1e-12)
    theta = Sm / c_total
    Q = Swx2 - (Sm**2 / c_total)
    denom = c_total - (Sc2 / c_total)
    tau2 = np.maximum(0.0, (Q - np.maximum(N - 1.0, 0.0)) / np.maximum(denom, 1e-12))

    return {
        "theta": np.asarray(theta, dtype=np.float64),
        "Q": np.asarray(Q, dtype=np.float64),
        "tau2": np.asarray(tau2, dtype=np.float64),
    }


def ecdf_overlap_integral(
    ecdf_view1: "ECDFView",
    ecdf_view2: "ECDFView",
    position_indices: np.ndarray,
    grid_size: int = 512,
) -> np.ndarray:
    """
    Continuous overlap between two ECDF-derived densities:
        overlap = integral_0^1 min(f1(x), f2(x)) dx

    PDF values are renormalized on the integration grid to guard against
    small numerical drift in PCHIP derivatives.
    """
    position_indices = np.asarray(position_indices, dtype=np.intp).ravel()
    grid = np.linspace(0.0, 1.0, grid_size, dtype=np.float64)
    trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")

    if hasattr(ecdf_view1, "_pdf_batch") and hasattr(ecdf_view2, "_pdf_batch"):
        from .core.distribution_views import ECDFView

        pdf1 = np.asarray(ecdf_view1._pdf_batch(position_indices, grid), dtype=np.float64)
        pdf2 = np.asarray(ecdf_view2._pdf_batch(position_indices, grid), dtype=np.float64)
        pdf_grid = ECDFView._pdf_integration_grid(grid)
    else:
        pdf_grid = grid
        pdf1 = np.zeros((len(position_indices), len(grid)), dtype=np.float64)
        pdf2 = np.zeros((len(position_indices), len(grid)), dtype=np.float64)
        for i, pos_idx in enumerate(position_indices):
            pdf1[i] = np.asarray([ecdf_view1._pdf(int(pos_idx), float(x)) for x in grid], dtype=np.float64)
            pdf2[i] = np.asarray([ecdf_view2._pdf(int(pos_idx), float(x)) for x in grid], dtype=np.float64)

    pdf1 = np.maximum(pdf1, 0.0)
    pdf2 = np.maximum(pdf2, 0.0)
    area1 = trapz(pdf1, pdf_grid, axis=1)
    area2 = trapz(pdf2, pdf_grid, axis=1)
    pdf1 = pdf1 / np.maximum(area1[:, None], 1e-12)
    pdf2 = pdf2 / np.maximum(area2[:, None], 1e-12)
    overlap = trapz(np.minimum(pdf1, pdf2), pdf_grid, axis=1)
    return np.clip(np.asarray(overlap, dtype=np.float64), 0.0, 1.0)


def _mean_level_weight(
    mean_level: np.ndarray,
    weight_mode: str = "saturating",
    k: float = 0.08,
) -> np.ndarray:
    """
    Weight factor for mean methylation level (μ̄ or max(μ1, μ2) depending on caller).
    Reduces effect_size at low methylation (e.g. CHH) to avoid inflated scores.

    Modes:
        "sqrt": f = sqrt(μ)
        "linear": f = μ
        "saturating": f = μ / (μ + k), k around 0.05--0.10
        "boundary": b(μ) = min(1, μ / τ); τ = k (e.g. 0.1). Down-weights when μ < τ;
            no penalty when μ ≥ τ. Preserves signals where one group has biologically
            meaningful methylation (use max(μ1, μ2) as input when using this mode).
    """
    mean_level = np.asarray(mean_level, dtype=np.float64).ravel()
    mu = np.clip(mean_level, 0.0, 1.0)
    tau_or_k = float(k)
    if weight_mode == "sqrt":
        w = np.sqrt(np.maximum(mu, 0.0))
    elif weight_mode == "linear":
        w = mu
    elif weight_mode == "saturating":
        w = np.where(mu > 0, mu / (mu + tau_or_k), 0.0)
    elif weight_mode == "boundary":
        # b(μ) = min(1, μ / τ): penalty when below τ, no penalty when ≥ τ
        w = np.where(tau_or_k > 0, np.minimum(1.0, mu / tau_or_k), np.ones_like(mu))
    else:
        w = np.ones_like(mu)
    return np.asarray(w, dtype=np.float64)


def effect_size_from_components(
    delta_mean: np.ndarray,
    overlap: np.ndarray,
    var1: np.ndarray,
    var2: np.ndarray,
    lambda_var: float = 2.0,
    mean_level: Optional[np.ndarray] = None,
    mean_level_weight: str = "saturating",
    mean_level_k: float = 0.08,
) -> Dict[str, np.ndarray]:
    """
    Canonical biological effect size used across MethylUtils/MethylDetector:

        effect_size = |delta_mean| * (1 - overlap) *
                      exp(-lambda_var * (sqrt(var1) + sqrt(var2)))

    Optional mean-level weight (e.g. for CHH): multiply by f(μ) where μ = mean_level.
    Modes: "sqrt", "linear", "saturating" (μ/(μ+k)), "boundary" (min(1, μ/τ); use max(μ1,μ2) as μ so one group ≥ τ removes penalty).
    """
    delta_mean = np.asarray(delta_mean, dtype=np.float64).ravel()
    overlap = np.asarray(overlap, dtype=np.float64).ravel()
    var1 = np.asarray(var1, dtype=np.float64).ravel()
    var2 = np.asarray(var2, dtype=np.float64).ravel()

    overlap = np.clip(overlap, 0.0, 1.0)
    var1 = np.maximum(var1, 0.0)
    var2 = np.maximum(var2, 0.0)
    reliability = np.exp(-float(lambda_var) * (np.sqrt(var1) + np.sqrt(var2)))
    effect_size = np.abs(delta_mean) * (1.0 - overlap) * reliability
    if mean_level is not None and mean_level.size == effect_size.size:
        w = _mean_level_weight(mean_level, weight_mode=mean_level_weight, k=mean_level_k)
        effect_size = effect_size * w
    effect_size = np.clip(effect_size, 0.0, 1.0)
    return {
        "effect_size": np.asarray(effect_size, dtype=np.float64),
        "reliability": np.asarray(reliability, dtype=np.float64),
    }


def ecdf_effect_size(
    delta_mean: np.ndarray,
    var1: np.ndarray,
    var2: np.ndarray,
    ecdf_view1: "ECDFView",
    ecdf_view2: "ECDFView",
    position_indices: np.ndarray,
    lambda_var: float = 2.0,
    grid_size: int = 512,
    mean_level: Optional[np.ndarray] = None,
    mean_level_weight: str = "saturating",
    mean_level_k: float = 0.08,
) -> Dict[str, np.ndarray]:
    """
    Compute continuous-ECDF overlap and the canonical biological effect size.
    Optional mean_level = (μ1+μ2)/2 multiplies effect_size by f(μ̄) to down-weight low methylation (e.g. CHH).
    """
    overlap = ecdf_overlap_integral(
        ecdf_view1=ecdf_view1,
        ecdf_view2=ecdf_view2,
        position_indices=position_indices,
        grid_size=grid_size,
    )
    effect = effect_size_from_components(
        delta_mean=delta_mean,
        overlap=overlap,
        var1=var1,
        var2=var2,
        lambda_var=lambda_var,
        mean_level=mean_level,
        mean_level_weight=mean_level_weight,
        mean_level_k=mean_level_k,
    )
    return {
        "overlap": overlap,
        "effect_size": effect["effect_size"],
        "reliability": effect["reliability"],
    }


def optimize_lambda_var(
    delta_mean: np.ndarray,
    overlap: np.ndarray,
    var1: np.ndarray,
    var2: np.ndarray,
    target_scores: np.ndarray,
    lambda_values: np.ndarray,
) -> Dict[str, Any]:
    """
    Choose lambda_var by maximizing Spearman correlation between effect_size
    and a caller-provided target score (for example 1 - p_value).
    """
    from scipy.stats import spearmanr

    target_scores = np.asarray(target_scores, dtype=np.float64).ravel()
    lambda_values = np.asarray(lambda_values, dtype=np.float64).ravel()
    best_lambda = float(lambda_values[0]) if len(lambda_values) else 0.0
    best_corr = -np.inf
    best_effect = None
    for lam in lambda_values:
        effect = effect_size_from_components(
            delta_mean=delta_mean,
            overlap=overlap,
            var1=var1,
            var2=var2,
            lambda_var=float(lam),
        )["effect_size"]
        if np.nanstd(effect) <= 0.0 or np.nanstd(target_scores) <= 0.0:
            corr = np.nan
        else:
            corr, _ = spearmanr(effect, target_scores)
        if np.isfinite(corr) and corr > best_corr:
            best_corr = float(corr)
            best_lambda = float(lam)
            best_effect = np.asarray(effect, dtype=np.float64)
    if best_effect is None:
        best_effect = effect_size_from_components(
            delta_mean=delta_mean,
            overlap=overlap,
            var1=var1,
            var2=var2,
            lambda_var=best_lambda,
        )["effect_size"]
        best_corr = float("nan")
    return {
        "lambda_var": best_lambda,
        "correlation": best_corr,
        "effect_size": np.asarray(best_effect, dtype=np.float64),
    }


def ecdf_bhattacharyya_trapezoidal_from_bin_counts(
    bc1: np.ndarray,
    bc2: np.ndarray,
    bin_edges: np.ndarray,
    grid_size: int = 256,
    prefer_gpu: Optional[bool] = None,
    use_gpu: Optional[bool] = None,
) -> np.ndarray:
    """
    Approximate Bhattacharyya coefficient by interpolating the ECDF on a dense
    uniform grid, then taking finite differences to estimate the PDF.

    This provides a smoother approximation when bins are unmatched or coarse,
    while avoiding the full PCHIP spline overhead.
    """
    from .array_backend import get_array_module, to_cpu

    if use_gpu is not None and prefer_gpu is None:
        prefer_gpu = use_gpu
    xp, _ = get_array_module(prefer_gpu)

    bc1 = xp.asarray(bc1, dtype=xp.float64)
    bc2 = xp.asarray(bc2, dtype=xp.float64)
    bin_edges = xp.asarray(bin_edges, dtype=xp.float64)

    squeeze = False
    if bc1.ndim == 1:
        bc1 = bc1.reshape(1, -1)
        bc2 = bc2.reshape(1, -1)
        squeeze = True

    n_pos = bc1.shape[0]
    total1 = xp.maximum(xp.sum(bc1, axis=1, keepdims=True), 1e-20)
    total2 = xp.maximum(xp.sum(bc2, axis=1, keepdims=True), 1e-20)

    cumsum1 = xp.cumsum(bc1, axis=1) / total1
    cumsum2 = xp.cumsum(bc2, axis=1) / total2

    zeros = xp.zeros((n_pos, 1), dtype=xp.float64)
    cdf1_edges = xp.concatenate([zeros, cumsum1], axis=1)
    cdf2_edges = xp.concatenate([zeros, cumsum2], axis=1)

    grid = xp.linspace(0.0, 1.0, grid_size, dtype=xp.float64)

    idx = xp.searchsorted(bin_edges, grid, side="right") - 1
    idx = xp.clip(idx, 0, len(bin_edges) - 2)

    widths = xp.maximum(bin_edges[idx + 1] - bin_edges[idx], 1e-20)
    t = (grid - bin_edges[idx]) / widths
    t = xp.clip(t, 0.0, 1.0)

    cdf1_grid = (1.0 - t) * cdf1_edges[:, idx] + t * cdf1_edges[:, idx + 1]
    cdf2_grid = (1.0 - t) * cdf2_edges[:, idx] + t * cdf2_edges[:, idx + 1]

    p1_grid = xp.maximum(xp.diff(cdf1_grid, axis=1), 0.0)
    p2_grid = xp.maximum(xp.diff(cdf2_grid, axis=1), 0.0)

    overlap = xp.sum(xp.sqrt(p1_grid * p2_grid), axis=1)

    out = xp.clip(overlap, 0.0, 1.0)
    if squeeze:
        out = out[0]

    return to_cpu(out)

# Dictionary of available aggregation methods
PVALUE_AGGREGATION_METHODS = {
    'fisher': aggregate_pvalues_fisher,
    'stouffer': aggregate_pvalues_stouffer,
    'lancaster': aggregate_pvalues_lancaster,
    'tippett': aggregate_pvalues_tippett,
    'edgington': aggregate_pvalues_edgington,
    'mudholkar_george': aggregate_pvalues_mudholkar_george,
    'simes': aggregate_pvalues_simes,
}


def beta_mom_estimation(
    n: np.ndarray,
    Sx: np.ndarray,
    Sx2: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Method of moments estimation for Beta distribution parameters.
    
    Args:
        n: Number of samples
        Sx: Sum of sample expectations
        Sx2: Sum of squared sample expectations
        
    Returns:
        alpha, beta arrays
    """
    from .gpu_detection import is_gpu_available

    use_gpu = is_gpu_available()
    xp = np
    n_np = np.asarray(n, dtype=np.float64)
    sx_np = np.asarray(Sx, dtype=np.float64)
    sx2_np = np.asarray(Sx2, dtype=np.float64)

    if use_gpu:
        try:
            import cupy as cp

            xp = cp
            n_work = xp.asarray(n_np, dtype=xp.float64)
            sx_work = xp.asarray(sx_np, dtype=xp.float64)
            sx2_work = xp.asarray(sx2_np, dtype=xp.float64)
        except Exception as e:
            logger.warning("beta_mom_estimation GPU setup failed; falling back to CPU: %s", e)
            use_gpu = False
            xp = np
            n_work = n_np
            sx_work = sx_np
            sx2_work = sx2_np
    else:
        n_work = n_np
        sx_work = sx_np
        sx2_work = sx2_np

    try:
        n_safe = xp.maximum(n_work, 1.0)
        mu = sx_work / n_safe

        # Sample variance (unbiased)
        denom = xp.maximum(n_work - 1.0, 1.0)
        var = xp.maximum((sx2_work - (sx_work**2) / n_safe) / denom, 1e-12)

        # Maximum possible theoretical variance for a variable in [0, 1] is mu*(1-mu)
        max_var = mu * (1.0 - mu)

        # Cap variance to slightly below max_var to avoid non-positive parameters
        var = xp.minimum(var, max_var - 1e-12)

        # Beta MOM formulas:
        # term = alpha + beta = (mu * (1 - mu) / var) - 1
        term = xp.maximum((mu * (1.0 - mu) / var) - 1.0, 1e-12)

        alpha = xp.maximum(mu * term, 1e-6)
        beta = xp.maximum((1.0 - mu) * term, 1e-6)
    except Exception as e:
        if not use_gpu:
            raise
        logger.warning("beta_mom_estimation GPU execution failed; falling back to CPU: %s", e)
        use_gpu = False
        n_safe = np.maximum(n_np, 1.0)
        mu = sx_np / n_safe
        denom = np.maximum(n_np - 1.0, 1.0)
        var = np.maximum((sx2_np - (sx_np**2) / n_safe) / denom, 1e-12)
        max_var = mu * (1.0 - mu)
        var = np.minimum(var, max_var - 1e-12)
        term = np.maximum((mu * (1.0 - mu) / var) - 1.0, 1e-12)
        alpha = np.maximum(mu * term, 1e-6)
        beta = np.maximum((1.0 - mu) * term, 1e-6)

    if use_gpu:
        from .metrics_core import DistanceCalculator

        calc = DistanceCalculator()
        try:
            return calc.cp.asnumpy(alpha), calc.cp.asnumpy(beta)
        except Exception:
            return np.asarray(alpha, dtype=np.float64), np.asarray(beta, dtype=np.float64)

    return np.asarray(alpha, dtype=np.float64), np.asarray(beta, dtype=np.float64)


__all__ = [
    "storey_qvalues",
    "stouffer_global_p",
    "aggregate_pvalues_fisher",
    "aggregate_pvalues_stouffer",
    "aggregate_pvalues_lancaster",
    "aggregate_pvalues_tippett",
    "aggregate_pvalues_edgington",
    "aggregate_pvalues_mudholkar_george",
    "aggregate_pvalues_simes",
    "PVALUE_AGGREGATION_METHODS",
    "ecdf_ks_statistic",
    "ecdf_ks_pvalue",
    "mann_whitney_from_bin_counts",
    "dl_heterogeneity",
    "ecdf_overlap_integral",
    "effect_size_from_components",
    "ecdf_effect_size",
    "optimize_lambda_var",
    "ecdf_bhattacharyya_trapezoidal_from_bin_counts",
    "beta_mom_estimation",
]
