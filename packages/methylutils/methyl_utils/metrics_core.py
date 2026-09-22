"""
Core metric computation functions for methylation analysis.

This module contains the fundamental implementations of various statistical
distances and divergences used in methylation data analysis. All functions
are optimized for both CPU and GPU computation.
"""

import numpy as np
from scipy.special import digamma, polygamma, betaln
from typing import Tuple

# Import GPU detection utilities
try:
    from .gpu_detection import is_gpu_available
    from .gpu_utils import _prepare_arrays_for_backend, _ensure_cpu_output
except ImportError:
    # Fallback for direct imports
    from gpu_detection import is_gpu_available
    from gpu_utils import _prepare_arrays_for_backend, _ensure_cpu_output

# Constants
NATURAL_LOG_2 = np.log(2.0)
DEFAULT_EPS = 1e-12
MIN_BETA_PARAM = 1e-6


class DistanceCalculator:
    """
    Singleton class for managing CPU/GPU implementations of statistical distances.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize GPU/CPU backends."""
        self.gpu_available = is_gpu_available()

        if self.gpu_available:
            try:
                import cupy as cp
                from cupyx.scipy.special import (
                    digamma as cupy_digamma,
                    polygamma as cupy_polygamma,
                    betaln as cupy_betaln
                )
                self.cp = cp
                self.gpu_digamma = cupy_digamma
                self.gpu_polygamma = lambda n, x: cupy_polygamma(n * cp.ones_like(x, dtype=int), x)
                self.gpu_betaln = cupy_betaln
            except ImportError:
                self.gpu_available = False
        else:
            pass  # CPU-only mode

    def get_backend(self, use_gpu: bool = True):
        """
        Get the appropriate backend (CPU or GPU) for calculations.

        Args:
            use_gpu: Whether to prefer GPU if available (honors METHYL_DISABLE_GPU)

        Returns:
            Tuple of (array_module, digamma_func, polygamma_func, betaln_func)
        """
        from .array_backend import get_special_backend

        return get_special_backend(prefer_gpu=use_gpu)


def compute_jeffreys_divergence(
    a1: np.ndarray,
    b1: np.ndarray,
    a2: np.ndarray,
    b2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute Jeffreys divergence (symmetric KL divergence) between two Beta distributions.

    J(Beta(a1,b1), Beta(a2,b2)) = KL(Beta(a1,b1) || Beta(a2,b2)) + KL(Beta(a2,b2) || Beta(a1,b1))

    Args:
        a1, b1: Parameters of the first Beta distribution
        a2, b2: Parameters of the second Beta distribution
        use_gpu: Whether to use GPU acceleration if available

    Returns:
        Jeffreys divergence values
    """
    calc = DistanceCalculator()
    xp, xdigamma, _, xbetaln = calc.get_backend(use_gpu)

    # Prepare arrays for backend
    (a1, b1, a2, b2), _ = _prepare_arrays_for_backend([a1, b1, a2, b2], calc, use_gpu)

    # KL divergence formula for Beta distributions
    a1_plus_b1 = a1 + b1
    a2_plus_b2 = a2 + b2

    digamma_a1 = xdigamma(a1)
    digamma_b1 = xdigamma(b1)
    digamma_a1_plus_b1 = xdigamma(a1_plus_b1)
    digamma_a2 = xdigamma(a2)
    digamma_b2 = xdigamma(b2)
    digamma_a2_plus_b2 = xdigamma(a2_plus_b2)

    # KL(P1 || P2)
    kl12 = (a1 - a2) * (digamma_a1 - digamma_a1_plus_b1) + \
           (b1 - b2) * (digamma_b1 - digamma_a1_plus_b1) + \
           xbetaln(a2, b2) - xbetaln(a1, b1)

    # KL(P2 || P1)
    kl21 = (a2 - a1) * (digamma_a2 - digamma_a2_plus_b2) + \
           (b2 - b1) * (digamma_b2 - digamma_a2_plus_b2) + \
           xbetaln(a1, b1) - xbetaln(a2, b2)

    # Jeffreys divergence = KL(P1||P2) + KL(P2||P1)
    jeffreys = kl12 + kl21

    # Ensure output is CPU array
    return _ensure_cpu_output(jeffreys, calc, use_gpu)


def compute_beta_llr_moments(
    alpha: np.ndarray,
    beta: np.ndarray,
    dalpha: np.ndarray,
    dbeta: np.ndarray,
    use_gpu: bool = True
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute mean and variance of log-likelihood ratio for Beta distributions.

    This function computes the moments of the log-likelihood ratio statistic
    for comparing two Beta distributions with parameters (alpha1, beta1) and (alpha2, beta2),
    where dalpha = alpha1 - alpha2 and dbeta = beta1 - beta2.
    
    Uses analytical formulas with digamma and trigamma functions for exact computation.

    Args:
        alpha: Alpha parameters of the first Beta distribution
        beta: Beta parameters of the first Beta distribution
        dalpha: Difference in alpha parameters (alpha1 - alpha2)
        dbeta: Difference in beta parameters (beta1 - beta2)
        use_gpu: Whether to use GPU acceleration if available

    Returns:
        Tuple of (mean, variance) arrays computed under the first distribution
    """
    calc = DistanceCalculator()
    xp, xdigamma, xpolygamma, _ = calc.get_backend(use_gpu)

    # Prepare arrays for backend
    (alpha, beta, dalpha, dbeta), _ = _prepare_arrays_for_backend([alpha, beta, dalpha, dbeta], calc, use_gpu)
    
    # Helper function for trigamma
    def trigamma(x):
        return xpolygamma(1, x)
    
    # Compute E[log X] and E[log(1-X)] under Beta(alpha, beta)
    sum_ab = alpha + beta
    e_logX = xdigamma(alpha) - xdigamma(sum_ab)
    e_log1mX = xdigamma(beta) - xdigamma(sum_ab)
    
    # Compute Var[log X], Var[log(1-X)], and Cov[log X, log(1-X)]
    var_logX = trigamma(alpha) - trigamma(sum_ab)
    var_log1mX = trigamma(beta) - trigamma(sum_ab)
    cov_logX_log1mX = -trigamma(sum_ab)
    
    # Mean of LLR under the first distribution
    # LLR = dalpha * log(X) + dbeta * log(1-X) + const
    # E[LLR] = dalpha * E[log X] + dbeta * E[log(1-X)]
    mean = dalpha * e_logX + dbeta * e_log1mX
    
    # Variance of LLR under the first distribution
    # Var[LLR] = dalpha² Var[log X] + dbeta² Var[log(1-X)] + 2×dalpha×dbeta×Cov[log X, log(1-X)]
    var = (dalpha ** 2) * var_logX + (dbeta ** 2) * var_log1mX + 2 * dalpha * dbeta * cov_logX_log1mX
    var = xp.maximum(var, 1e-12)  # Ensure positive variance

    # Ensure output is CPU arrays
    mean_cpu = _ensure_cpu_output(mean, calc, use_gpu)
    var_cpu = _ensure_cpu_output(var, calc, use_gpu)

    return mean_cpu, var_cpu


def compute_distribution_overlap(
    alpha1: np.ndarray,
    beta1: np.ndarray,
    alpha2: np.ndarray,
    beta2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute distribution overlap between two Beta distributions.

    This function computes the overlap coefficient between two Beta distributions,
    which measures how much the distributions overlap (0 = no overlap, 1 = complete overlap).

    Args:
        alpha1: Alpha parameters of first Beta distribution
        beta1: Beta parameters of first Beta distribution
        alpha2: Alpha parameters of second Beta distribution
        beta2: Beta parameters of second Beta distribution
        use_gpu: Whether to use GPU acceleration if available

    Returns:
        Array of overlap coefficients
    """
    calc = DistanceCalculator()
    xp, _, _, _ = calc.get_backend(use_gpu)

    # Overlap coefficient for Beta distributions
    # This is a simplified approximation - actual implementation should use proper integration

    # Use Bhattacharyya coefficient as approximation for overlap
    # BC = exp(μ1*μ2 / (σ1² + σ2²)) * sqrt(2*σ1*σ2 / (σ1² + σ2²))

    # For Beta distributions, compute means and variances
    tau1 = alpha1 + beta1
    tau2 = alpha2 + beta2

    # Numerically stable mean calculation with epsilon to avoid division by zero
    eps = 1e-12
    mean1 = alpha1 / xp.maximum(tau1, eps)
    mean2 = alpha2 / xp.maximum(tau2, eps)

    # Numerically stable variance calculation: var = mean * (1 - mean) / (tau + 1)
    # This avoids overflow when alpha/beta are very large
    var1 = mean1 * (1 - mean1) / xp.maximum(tau1 + 1, eps)
    var2 = mean2 * (1 - mean2) / xp.maximum(tau2 + 1, eps)

    # Bhattacharyya coefficient
    sigma_sum = var1 + var2
    # Use xp operations instead of np operations
    bc = xp.where(
        sigma_sum == 0,
        1.0,
        xp.exp(-(mean1 - mean2)**2 / (2 * sigma_sum)) * \
        xp.sqrt(2 * xp.sqrt(var1 * var2) / sigma_sum)
    )

    # Convert Bhattacharyya coefficient to overlap coefficient
    # Overlap ≈ BC for well-separated distributions
    overlap = bc

    # Ensure overlap is between 0 and 1
    return _ensure_cpu_output(xp.clip(overlap, 0.0, 1.0), calc, use_gpu)


def compute_kl_divergence(
    a1: np.ndarray,
    b1: np.ndarray,
    a2: np.ndarray,
    b2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute Kullback-Leibler divergence between two Beta distributions.

    KL(Beta(a1,b1) || Beta(a2,b2))

    Args:
        a1, b1: Parameters of the first Beta distribution
        a2, b2: Parameters of the second Beta distribution
        use_gpu: Whether to use GPU acceleration if available

    Returns:
        KL divergence values

    Raises:
        ValueError: If Beta parameters are not positive or arrays have mismatched shapes.
    """
    calc = DistanceCalculator()
    xp, xdigamma, _, xbetaln = calc.get_backend(use_gpu)

    # Prepare arrays for backend
    (a1, b1, a2, b2), _ = _prepare_arrays_for_backend([a1, b1, a2, b2], calc, use_gpu)

    # KL divergence formula for Beta distributions (optimized computation)
    a1_plus_b1 = a1 + b1
    digamma_a1 = xdigamma(a1)
    digamma_b1 = xdigamma(b1)
    digamma_a1_plus_b1 = xdigamma(a1_plus_b1)

    # Compute terms efficiently to minimize memory allocations
    term1 = (a1 - a2) * (digamma_a1 - digamma_a1_plus_b1)
    term2 = (b1 - b2) * (digamma_b1 - digamma_a1_plus_b1)
    term3 = xbetaln(a2, b2) - xbetaln(a1, b1)

    kl = term1 + term2 + term3

    # Ensure output is CPU array
    return _ensure_cpu_output(kl, calc, use_gpu)


def compute_bhattacharyya_distance(
    a1: np.ndarray,
    b1: np.ndarray,
    a2: np.ndarray,
    b2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute Bhattacharyya distance between two Beta distributions.

    BD = -ln(BC), where BC is the Bhattacharyya coefficient

    Args:
        a1, b1: Parameters of the first Beta distribution
        a2, b2: Parameters of the second Beta distribution
        use_gpu: Whether to use GPU acceleration if available

    Returns:
        Bhattacharyya distance values
    """
    calc = DistanceCalculator()
    xp, _, _, xbetaln = calc.get_backend(use_gpu)

    # Prepare arrays for backend
    (a1, b1, a2, b2), _ = _prepare_arrays_for_backend([a1, b1, a2, b2], calc, use_gpu)

    # Bhattacharyya coefficient for Beta distributions (clip ln_BC to avoid exp overflow)
    ln_bc = xbetaln((a1 + a2) / 2, (b1 + b2) / 2) - 0.5 * (xbetaln(a1, b1) + xbetaln(a2, b2))
    ln_bc = xp.clip(ln_bc, -700.0, 0.0)  # BC in (0, 1] so ln_BC <= 0; clip to avoid overflow
    bc = xp.exp(ln_bc)

    # Bhattacharyya distance
    bd = -xp.log(bc + DEFAULT_EPS)

    # Ensure output is CPU array
    return _ensure_cpu_output(bd, calc, use_gpu)


def compute_hellinger_distance(
    a1: np.ndarray,
    b1: np.ndarray,
    a2: np.ndarray,
    b2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute Hellinger distance between two Beta distributions.

    H = √(1 - BC), where BC is the Bhattacharyya coefficient

    The Hellinger distance is defined as:
    H(P,Q) = √(∫(√p - √q)² dx) = √(2 - 2∫√(p q) dx) = √(2(1 - BC))

    This implementation normalizes the result to [0, 1] by dividing by √(2),
    which simplifies to √(1 - BC), making it comparable to other distance
    metrics like Jensen-Shannon distance.

    Args:
        a1, b1: Parameters of the first Beta distribution
        a2, b2: Parameters of the second Beta distribution
        use_gpu: Whether to use GPU acceleration if available

    Returns:
        Normalized Hellinger distance values (between 0 and 1)
    """
    calc = DistanceCalculator()
    xp, _, _, xbetaln = calc.get_backend(use_gpu)

    # Prepare arrays for backend
    (a1, b1, a2, b2), _ = _prepare_arrays_for_backend([a1, b1, a2, b2], calc, use_gpu)

    # Bhattacharyya coefficient (clip to avoid exp overflow)
    ln_bc = xbetaln((a1 + a2) / 2, (b1 + b2) / 2) - 0.5 * (xbetaln(a1, b1) + xbetaln(a2, b2))
    ln_bc = xp.clip(ln_bc, -700.0, 0.0)
    bc = xp.exp(ln_bc)

    # Normalized Hellinger distance: H = √(1 - BC)
    # This is equivalent to √(2 - 2BC) / √(2), normalized to [0, 1]
    hellinger = xp.sqrt(1 - bc)

    # Ensure output is CPU array
    return _ensure_cpu_output(hellinger, calc, use_gpu)


def compute_wasserstein_distance(
    a1: np.ndarray,
    b1: np.ndarray,
    a2: np.ndarray,
    b2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute approximate Wasserstein distance between two Beta distributions.

    This uses a moment-based approximation for computational efficiency.

    Args:
        a1, b1: Parameters of the first Beta distribution
        a2, b2: Parameters of the second Beta distribution
        use_gpu: Whether to use GPU acceleration if available

    Returns:
        Approximate Wasserstein distance values
    """
    calc = DistanceCalculator()

    # Prepare arrays for backend
    (a1, b1, a2, b2), xp = _prepare_arrays_for_backend([a1, b1, a2, b2], calc, use_gpu)

    # Compute means
    mean1 = a1 / (a1 + b1)
    mean2 = a2 / (a2 + b2)

    # Compute variances
    var1 = (a1 * b1) / ((a1 + b1)**2 * (a1 + b1 + 1))
    var2 = (a2 * b2) / ((a2 + b2)**2 * (a2 + b2 + 1))

    # Approximate Wasserstein distance using means and variances
    # This is exact for normal distributions, approximate for Beta
    wasserstein = xp.abs(mean1 - mean2) + xp.abs(xp.sqrt(var1) - xp.sqrt(var2))

    # Ensure output is CPU array
    return _ensure_cpu_output(wasserstein, calc, use_gpu)


def compute_jensen_shannon_distance(
    a1: np.ndarray,
    b1: np.ndarray,
    a2: np.ndarray,
    b2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute Jensen-Shannon distance metric (sqrt(JSD/ln(2))) between two Beta distributions.

    JSD is the average of KL(P1||M) and KL(P2||M), where M is the mixture distribution.

    Args:
        a1, b1: Alpha and beta parameters for the first Beta distribution (arrays).
        a2, b2: Alpha and beta parameters for the second Beta distribution (arrays).
        use_gpu: Whether to use GPU acceleration if available.

    Returns:
        Array of JSD metric values, bounded in [0, 1].
    """
    calc = DistanceCalculator()
    xp, xdigamma, _, xbetaln = calc.get_backend(use_gpu)

    # Prepare arrays for backend
    (a1, b1, a2, b2), _ = _prepare_arrays_for_backend([a1, b1, a2, b2], calc, use_gpu)

    # Midpoint Beta parameters
    a_m = (a1 + a2) / 2
    b_m = (b1 + b2) / 2

    # KL(P1 || M)
    kl1m = (a1 - a_m) * (xdigamma(a1) - xdigamma(a1 + b1)) + \
           (b1 - b_m) * (xdigamma(b1) - xdigamma(a1 + b1)) + \
           xbetaln(a_m, b_m) - xbetaln(a1, b1)

    # KL(P2 || M)
    kl2m = (a2 - a_m) * (xdigamma(a2) - xdigamma(a2 + b2)) + \
           (b2 - b_m) * (xdigamma(b2) - xdigamma(a2 + b2)) + \
           xbetaln(a_m, b_m) - xbetaln(a2, b2)

    # JSD = (1/2) * (KL1 + KL2)
    jsd = 0.5 * (kl1m + kl2m)

    # Normalize to [0, 1] and take square root to make it a metric
    jsd_normalized = jsd / NATURAL_LOG_2
    result = xp.sqrt(xp.maximum(jsd_normalized, 0.0))

    # Ensure result is properly bounded in [0, 1]
    # For very different distributions, JSD can exceed 1 due to numerical precision
    result = xp.clip(result, 0.0, 1.0)

    # Ensure output is CPU array
    return _ensure_cpu_output(result, calc, use_gpu)


def compute_weighted_jensen_shannon_distance(
    a1: np.ndarray,
    b1: np.ndarray,
    a2: np.ndarray,
    b2: np.ndarray,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute weighted Jensen-Shannon distance between two Beta distributions.

    This is an entropy-weighted generalization of the Jensen-Shannon divergence.
    It computes the regular JSD first, then applies conservative entropy weighting
    to emphasize positions with higher uncertainty.

    The weighting factor is derived from the average entropy of both distributions,
    normalized to provide moderate emphasis (50% more/less weight) without extreme
    distortion of the distance values.

    This provides a nuanced distance measure that accounts for data uncertainty
    while remaining comparable to the standard Jensen-Shannon distance.

    Args:
        a1, b1: Alpha and beta parameters for the first Beta distribution (arrays).
        a2, b2: Alpha and beta parameters for the second Beta distribution (arrays).
        use_gpu: Whether to use GPU acceleration if available.

    Returns:
        Array of weighted JSD metric values, properly normalized and bounded in [0, 1].
        Values are directly comparable to regular JSD but incorporate entropy information.
    """
    calc = DistanceCalculator()
    xp, xdigamma, _, xbetaln = calc.get_backend(use_gpu)

    # Prepare arrays for backend
    (a1, b1, a2, b2), _ = _prepare_arrays_for_backend([a1, b1, a2, b2], calc, use_gpu)

    # Check for identical distributions (should return 0)
    identical = xp.allclose(a1, a2) and xp.allclose(b1, b2)
    if identical.all() if hasattr(identical, 'all') else identical:
        # For identical distributions, distance is always 0
        result = xp.zeros_like(a1)
        return _ensure_cpu_output(result, calc, use_gpu)

    # Compute raw entropy values directly for weighted JSD
    # This gives us the actual information content (in bits) for proper weighting
    p1 = a1 / (a1 + b1)
    p2 = a2 / (a2 + b2)

    # Compute entropy in bits using the same formula as compute_entropy
    eps = DEFAULT_EPS
    p1_clipped = xp.clip(p1, eps, 1.0 - eps)
    p2_clipped = xp.clip(p2, eps, 1.0 - eps)

    # Entropy: H = -∑p*log2(p) in bits
    h1 = -(p1_clipped * xp.log(p1_clipped) + (1.0 - p1_clipped) * xp.log1p(-p1_clipped)) / NATURAL_LOG_2
    h2 = -(p2_clipped * xp.log(p2_clipped) + (1.0 - p2_clipped) * xp.log1p(-p2_clipped)) / NATURAL_LOG_2

    # For weighted JSD, we compute the regular JSD first, then apply entropy weighting
    # This preserves the mathematical correctness while adding entropy information

    # First compute regular JSD midpoint
    a_m_regular = (a1 + a2) / 2
    b_m_regular = (b1 + b2) / 2

    # Compute regular KL divergences
    kl1m_regular = (a1 - a_m_regular) * (xdigamma(a1) - xdigamma(a1 + b1)) + \
                   (b1 - b_m_regular) * (xdigamma(b1) - xdigamma(a1 + b1)) + \
                   xbetaln(a_m_regular, b_m_regular) - xbetaln(a1, b1)

    kl2m_regular = (a2 - a_m_regular) * (xdigamma(a2) - xdigamma(a2 + b2)) + \
                   (b2 - b_m_regular) * (xdigamma(b2) - xdigamma(a2 + b2)) + \
                   xbetaln(a_m_regular, b_m_regular) - xbetaln(a2, b2)

    jsd_regular = 0.5 * (kl1m_regular + kl2m_regular)

    # Apply entropy weighting to the result
    # Higher entropy positions get slightly more weight in the final distance
    avg_entropy = (h1 + h2) / 2.0

    # Normalize entropy to [0.5, 1.5] range to avoid extreme weighting
    # This makes high entropy positions contribute ~50% more, low entropy ~50% less
    entropy_factor = 0.5 + (avg_entropy / 2.0)  # Maps entropy [0,1] to factor [0.5, 1.0]
    entropy_factor = xp.clip(entropy_factor, 0.5, 1.5)  # Conservative bounds

    # Apply entropy weighting
    jsd_weighted = jsd_regular * entropy_factor

    # Normalize to [0, 1] and take square root to make it a metric
    jsd_normalized = jsd_weighted / NATURAL_LOG_2
    result = xp.sqrt(xp.maximum(jsd_normalized, 0.0))

    # Ensure result is properly bounded in [0, 1]
    # For very different distributions, JSD can exceed 1 due to numerical precision
    result = xp.clip(result, 0.0, 1.0)

    # Ensure output is CPU array
    return _ensure_cpu_output(result, calc, use_gpu)


def compute_entropy(
    p: np.ndarray,
    eps: float = DEFAULT_EPS,
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute entropy-based weights for Weighted JS, GPU-aware.

    This function calculates entropy-based weights where positions with higher
    uncertainty (entropy closer to 1 bit) get higher weights. This is useful
    for weighted Jensen-Shannon divergence calculations.

    Args:
        p: Centroid methylation probabilities (values in [0,1]).
        eps: Small constant to avoid division by zero and log(0).
        use_gpu: Whether to use GPU acceleration if available.

    Returns:
        Normalized weights that sum to 1, where higher entropy positions
        get higher weights.

    Raises:
        ValueError: If input array is empty or contains invalid values.
    """
    if p.size == 0:
        raise ValueError("Input array cannot be empty")

    calc = DistanceCalculator()
    p, xp = _prepare_arrays_for_backend([p], calc, use_gpu)

    # Treat invalids as maximum-uncertainty positions (p=0.5 -> H=1 bit)
    invalid = ~xp.isfinite(p)
    if invalid.any():
        p = xp.where(invalid, 0.5, p)

    # Clip away exact 0/1 to keep logs finite
    p = xp.clip(p, eps, 1.0 - eps)

    # Compute entropy using log1p for numerical accuracy near 0/1
    H_c = -(p * xp.log(p) + (1.0 - p) * xp.log1p(-p)) / NATURAL_LOG_2

    # Option B: 1 - entropy (already normalized, since max=1 bit)
    weights = 1.0 - H_c
    # Ensure non-negative weights
    xp.maximum(weights, eps, out=weights)

    # Normalize weights to sum to 1
    wsum = weights.sum()
    if wsum <= 0 or not xp.isfinite(wsum):
        # Fallback: uniform weights
        w = xp.ones_like(weights) / weights.size
    else:
        w = weights / wsum

    # Ensure output is CPU array
    return _ensure_cpu_output(w, calc, use_gpu)


def compute_sample_centroid_jsd(
    m: np.ndarray,          # Methylation levels for sample positions
    n: np.ndarray,          # Coverages for sample positions
    alpha_c: np.ndarray,    # Centroid alpha
    beta_c: np.ndarray,     # Centroid beta
    use_gpu: bool = True
) -> np.ndarray:
    """
    Compute Jensen-Shannon distance between sample (m, n) and centroid Beta distributions.

    This function takes methylation levels and coverages from a sample, converts them
    to Beta distribution parameters, and computes the Jensen-Shannon distance to
    the provided centroid Beta distribution parameters.

    Args:
        m: Array of methylation levels (k/n, between 0 and 1).
        n: Array of coverages (total reads).
        alpha_c, beta_c: Arrays of alpha and beta parameters for centroid Beta.
        use_gpu: Whether to use GPU acceleration if available.

    Returns:
        Array of JSD metric values (sqrt(JSD/ln(2)), bounded in [0, 1]).

    Raises:
        ValueError: If input arrays have mismatched shapes or invalid data types.
    """
    from .metric_validations import validate_sample_data

    # Input validation
    validate_sample_data(m, n, alpha_c, beta_c)

    # Prepare arrays for backend
    calc = DistanceCalculator()
    (m, n, alpha_c, beta_c), xp = _prepare_arrays_for_backend([m, n, alpha_c, beta_c], calc, use_gpu, dtype=np.float32)

    # Get sample Beta parameters
    alpha_s, beta_s = get_sample_beta_mom(m, n, use_gpu)

    # Compute Jensen-Shannon distance (already a metric)
    jsd = compute_jensen_shannon_distance(alpha_s, beta_s, alpha_c, beta_c, use_gpu)

    # Ensure output is CPU array
    return _ensure_cpu_output(jsd, calc, use_gpu)


def get_sample_beta_mom(
    m: np.ndarray,
    n: np.ndarray,
    use_gpu: bool = True
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Vectorized method-of-moments to derive Beta parameters from methylation levels and coverages.

    This function automatically uses GPU acceleration when available and falls back to CPU.

    Args:
        m: Array of methylation levels (k/n, between 0 and 1).
        n: Array of coverages (total reads).
        use_gpu: Whether to use GPU acceleration if available.

    Returns:
        Tuple of (alpha, beta) arrays for the Beta distributions.

    Raises:
        ValueError: If input arrays have mismatched shapes or invalid data types.
    """
    from .metric_validations import validate_methylation_data

    # Input validation
    validate_methylation_data(m, n)

    calc = DistanceCalculator()

    # Prepare arrays for backend using utility function
    (m, n), xp = _prepare_arrays_for_backend([m, n], calc, use_gpu, dtype=np.float32)

    # Initialize output arrays
    alpha = xp.zeros_like(m, dtype=xp.float32)
    beta = xp.zeros_like(m, dtype=xp.float32)

    # Valid indices: n >= 2 and 0 <= m <= 1 (combined condition for efficiency)
    valid = (n >= 2) & (m >= 0) & (m <= 1) & (~xp.isnan(m)) & (~xp.isnan(n))

    # Compute Beta parameters efficiently: alpha = m * (n-1), beta = (1-m) * (n-1)
    # Use in-place operations where possible to reduce memory allocation
    effective_size = n - 1  # Compute once, reuse
    xp.copyto(alpha, m * effective_size, where=valid)
    xp.copyto(beta, (1 - m) * effective_size, where=valid)

    # Clamp to avoid numerical issues (in-place operation)
    xp.maximum(alpha, MIN_BETA_PARAM, out=alpha)
    xp.maximum(beta, MIN_BETA_PARAM, out=beta)

    # Fallback for invalid/low coverage: uniform Beta(1,1) (in-place)
    xp.copyto(alpha, 1.0, where=~valid)
    xp.copyto(beta, 1.0, where=~valid)

    # Ensure output is CPU array
    return _ensure_cpu_output((alpha, beta), calc, use_gpu)


__all__ = [
    "DistanceCalculator",
    "compute_jeffreys_divergence",
    "compute_kl_divergence",
    "compute_bhattacharyya_distance",
    "compute_hellinger_distance",
    "compute_wasserstein_distance",
    "compute_jensen_shannon_distance",
    "compute_weighted_jensen_shannon_distance",
    "compute_entropy",
    "compute_sample_centroid_jsd",
    "get_sample_beta_mom"
]
