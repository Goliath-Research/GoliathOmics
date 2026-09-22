"""
Methylation Data Transformations

Advanced preprocessing transformations for methylation data to improve
downstream analysis like DMP detection and classification.

Current transformations:
- EAT (Entropy-weighted Asymmetry Transformation): Reweights methylation loci
  based on Beta distribution shape differences to emphasize biologically
  meaningful differences and de-emphasize high-entropy loci.
"""

from __future__ import annotations

import numpy as np
from scipy.special import betaln, digamma, gammaln
from typing import Optional, Union, Tuple
import logging

# For GPU support (optional)
try:
    import cupy as cp
    CUPY_AVAILABLE = True
except ImportError:
    CUPY_AVAILABLE = False

# Import MethylUtils utilities
from .metric_validations import validate_beta_parameters, validate_array_shapes

logger = logging.getLogger(__name__)


def get_xp(use_gpu: bool = False) -> type:
    """Get array backend (NumPy or CuPy); honors METHYL_DISABLE_GPU when use_gpu=True."""
    from .array_backend import get_array_module

    xp, _ = get_array_module(prefer_gpu=use_gpu)
    return xp


def validate_eat_inputs(alpha_H: np.ndarray, beta_H: np.ndarray,
                       alpha_C: np.ndarray, beta_C: np.ndarray) -> None:
    """Validate inputs for EAT transformation."""
    validate_beta_parameters(alpha_H, beta_H, "Healthy")
    validate_beta_parameters(alpha_C, beta_C, "Cancer")
    validate_array_shapes([alpha_H, beta_H, alpha_C, beta_C], "EAT inputs")

    if alpha_H.shape != alpha_C.shape:
        raise ValueError(f"Healthy and Cancer parameter shapes mismatch: {alpha_H.shape} vs {alpha_C.shape}")


def beta_mean(alpha: np.ndarray, beta: np.ndarray, eps: float = 1e-12, xp=np) -> np.ndarray:
    """Compute Beta distribution mean: a/(a+B)."""
    denom = xp.maximum(alpha + beta, eps)
    return alpha / denom


def beta_entropy(alpha: np.ndarray, beta: np.ndarray, eps: float = 1e-12,
                use_loggamma: bool = True, xp=np) -> np.ndarray:
    """
    Compute Beta distribution entropy: ln(B(a,b)) - (a-1)ψ(a) - (b-1)ψ(b) + (a+b-2)ψ(a+b)

    Uses log-gamma approximation for numerical stability with large parameters.
    """
    a = xp.maximum(alpha, eps)
    b = xp.maximum(beta, eps)
    ab = a + b

    if use_loggamma:
        # Use log-gamma for numerical stability: ln B(a,b) = ln Γ(a) + ln Γ(b) - ln Γ(a+b)
        ln_beta = gammaln(a) + gammaln(b) - gammaln(ab)
    else:
        ln_beta = betaln(a, b)

    # Entropy = ln B(a,b) - (a-1)ψ(a) - (b-1)ψ(b) + (a+b-2)ψ(a+b)
    entropy = (
        ln_beta
        - (a - 1.0) * digamma(a)
        - (b - 1.0) * digamma(b)
        + (ab - 2.0) * digamma(ab)
    )

    return entropy


def beta_asymmetry(alpha: np.ndarray, beta: np.ndarray, eps: float = 1e-12, xp=np) -> np.ndarray:
    """
    Compute Beta distribution asymmetry index: (a - b) / (a + b)

    Positive values indicate skew toward methylation (right side),
    negative values indicate skew toward unmethylation (left side).
    """
    a = xp.maximum(alpha, eps)
    b = xp.maximum(beta, eps)
    denom = xp.maximum(a + b, eps)
    return (a - b) / denom


def normal_approx_entropy(mean: np.ndarray, var: np.ndarray, eps: float = 1e-12, xp=np) -> np.ndarray:
    """Normal approximation entropy: 0.5 * ln(2πe * variance)."""
    return 0.5 * xp.log(2 * xp.pi * xp.e * xp.maximum(var, eps))


def normal_approx_asymmetry(mean: np.ndarray, var: np.ndarray, eps: float = 1e-12, xp=np) -> np.ndarray:
    """Approximate asymmetry for Normal: (mean - 0.5) / sqrt(variance)."""
    return (mean - 0.5) / xp.sqrt(xp.maximum(var, eps))


def compute_eat_T(alpha_H: np.ndarray, beta_H: np.ndarray,
                 alpha_C: np.ndarray, beta_C: np.ndarray,
                 gamma: float = 1.0, clip_T: Optional[float] = None,
                 eps: float = 1e-12, low_tau_threshold: float = 5.0,
                 use_loggamma: bool = True, use_gpu: bool = False) -> np.ndarray:
    """
    Compute EAT distortion vector T for per-locus reweighting.

    T_i = Δμ_i * (1 + |ΔA_i|) * exp(-γ * |ΔH_i|)

    Where:
    - Δμ_i: Mean difference (cancer - healthy)
    - ΔA_i: Asymmetry difference |A_cancer - A_healthy|
    - ΔH_i: Entropy difference |H_cancer - H_healthy|
    - γ: Entropy damping parameter

    Parameters
    ----------
    alpha_H, beta_H : array-like
        Healthy centroid Beta parameters [L]
    alpha_C, beta_C : array-like
        Cancer centroid Beta parameters [L]
    gamma : float
        Entropy damping parameter (higher = stronger penalty for similar entropy)
    clip_T : float or None
        Clip T values to [-clip_T, +clip_T] to prevent extreme reweighting
    eps : float
        Numerical stability epsilon
    low_tau_threshold : float
        Fallback to Normal approximation when concentration < threshold
    use_loggamma : bool
        Use gammaln for betaln approximation (numerical stability)
    use_gpu : bool
        Use CuPy for GPU acceleration if available

    Returns
    -------
    T : ndarray, shape [L]
        EAT distortion vector for locus reweighting
    """
    xp = get_xp(use_gpu)

    # Convert to arrays and validate
    alpha_H = xp.asarray(alpha_H, dtype=xp.float32)
    beta_H = xp.asarray(beta_H, dtype=xp.float32)
    alpha_C = xp.asarray(alpha_C, dtype=xp.float32)
    beta_C = xp.asarray(beta_C, dtype=xp.float32)
    validate_eat_inputs(alpha_H, beta_H, alpha_C, beta_C)

    # Compute concentration parameters (tau = α + β)
    tau_H = alpha_H + beta_H
    tau_C = alpha_C + beta_C

    # Masks for low-concentration loci (fallback to Normal approximation)
    low_tau_H = tau_H < low_tau_threshold
    low_tau_C = tau_C < low_tau_threshold

    # Compute means (shared for Beta and Normal)
    mu_H = beta_mean(alpha_H, beta_H, eps, xp)
    mu_C = beta_mean(alpha_C, beta_C, eps, xp)
    delta_mu = mu_C - mu_H

    # Compute asymmetries
    A_H = beta_asymmetry(alpha_H, beta_H, eps, xp)
    A_C = beta_asymmetry(alpha_C, beta_C, eps, xp)

    # Fallback to Normal approximation for low-coverage loci
    if xp.any(low_tau_H):
        var_H = mu_H * (1 - mu_H) / xp.maximum(tau_H, eps)  # Beta variance approximation
        A_H = xp.where(low_tau_H, normal_approx_asymmetry(mu_H, var_H, eps, xp), A_H)

    if xp.any(low_tau_C):
        var_C = mu_C * (1 - mu_C) / xp.maximum(tau_C, eps)
        A_C = xp.where(low_tau_C, normal_approx_asymmetry(mu_C, var_C, eps, xp), A_C)

    delta_A = xp.abs(A_C - A_H)

    # Compute entropies
    H_H = beta_entropy(alpha_H, beta_H, eps, use_loggamma, xp)
    H_C = beta_entropy(alpha_C, beta_C, eps, use_loggamma, xp)

    # Fallback to Normal approximation for low-coverage loci
    if xp.any(low_tau_H):
        var_H = mu_H * (1 - mu_H) / xp.maximum(tau_H, eps)
        H_H = xp.where(low_tau_H, normal_approx_entropy(mu_H, var_H, eps, xp), H_H)

    if xp.any(low_tau_C):
        var_C = mu_C * (1 - mu_C) / xp.maximum(tau_C, eps)
        H_C = xp.where(low_tau_C, normal_approx_entropy(mu_C, var_C, eps, xp), H_C)

    delta_H = xp.abs(H_C - H_H)

    # Compute EAT distortion: T = Δμ * (1 + |ΔA|) * exp(-γ * |ΔH|)
    T = delta_mu * (1.0 + delta_A) * xp.exp(-gamma * delta_H)

    # Optional clipping to prevent extreme reweighting
    if clip_T is not None:
        T = xp.clip(T, -clip_T, clip_T)

    # Return as NumPy array for consistency
    return xp.asnumpy(T) if use_gpu and CUPY_AVAILABLE else T


def apply_eat_transform(X: np.ndarray, T: np.ndarray,
                       norm: Optional[str] = "l2", eps: float = 1e-12,
                       use_gpu: bool = False) -> np.ndarray:
    """
    Apply EAT transformation to methylation data matrix.

    Reweights each locus by elementwise multiplication with distortion vector T,
    then optionally normalizes each sample.

    Parameters
    ----------
    X : ndarray, shape [L, N] or [N, L]
        Methylation data matrix (values in [0,1])
    T : ndarray, shape [L]
        EAT distortion vector
    norm : {'l1', 'l2', 'minmax', 'zscore', None}
        Per-sample normalization after reweighting
    eps : float
        Numerical stability epsilon
    use_gpu : bool
        Use CuPy for GPU acceleration if available

    Returns
    -------
    X_transformed : ndarray, same shape as X
        EAT-transformed methylation data
    """
    xp = get_xp(use_gpu)

    X = xp.asarray(X, dtype=xp.float32)
    T = xp.asarray(T, dtype=xp.float32)

    # Determine orientation
    if X.shape[0] == T.shape[0]:
        loci_axis = 0  # X is [L, N]
        sample_axis = 1
        Xw = X * T[:, None]
    elif X.shape[1] == T.shape[0]:
        loci_axis = 1  # X is [N, L]
        sample_axis = 0
        Xw = X * T[None, :]
    else:
        raise ValueError(f"X shape {X.shape} incompatible with T shape {T.shape}")

    # Apply normalization if requested
    if norm is not None:
        if norm == "l2":
            denom = xp.linalg.norm(Xw, axis=loci_axis, keepdims=True)
            denom = xp.maximum(denom, eps)
            Xw = Xw / denom
        elif norm == "l1":
            denom = xp.sum(xp.abs(Xw), axis=loci_axis, keepdims=True)
            denom = xp.maximum(denom, eps)
            Xw = Xw / denom
        elif norm == "zscore":
            mean = xp.mean(Xw, axis=loci_axis, keepdims=True)
            std = xp.std(Xw, axis=loci_axis, ddof=1, keepdims=True)
            std = xp.maximum(std, eps)
            Xw = (Xw - mean) / std
        elif norm == "minmax":
            min_val = xp.min(Xw, axis=loci_axis, keepdims=True)
            max_val = xp.max(Xw, axis=loci_axis, keepdims=True)
            denom = xp.maximum(max_val - min_val, eps)
            Xw = (Xw - min_val) / denom
        else:
            raise ValueError(f"Unknown normalization: {norm}. Must be one of "
                           "{'l1', 'l2', 'minmax', 'zscore', None}")

    # Return as NumPy array for consistency
    return xp.asnumpy(Xw) if use_gpu and CUPY_AVAILABLE else Xw


def eat_transform_from_betas(X: np.ndarray,
                           alpha_H: np.ndarray, beta_H: np.ndarray,
                           alpha_C: np.ndarray, beta_C: np.ndarray,
                           gamma: float = 1.0, norm: Optional[str] = "l2",
                           clip_T: Optional[float] = None, eps: float = 1e-12,
                           low_tau_threshold: float = 5.0, use_loggamma: bool = True,
                           use_gpu: bool = False) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convenience function: compute EAT distortion and apply transformation.

    Parameters
    ----------
    X : ndarray
        Methylation data matrix [L, N] or [N, L]
    alpha_H, beta_H : ndarray
        Healthy centroid Beta parameters [L]
    alpha_C, beta_C : ndarray
        Cancer centroid Beta parameters [L]
    gamma : float
        EAT entropy damping parameter
    norm : str or None
        Post-transformation normalization
    clip_T : float or None
        Optional T value clipping
    eps : float
        Numerical stability epsilon
    low_tau_threshold : float
        Normal approximation threshold
    use_loggamma : bool
        Use gammaln for numerical stability
    use_gpu : bool
        Enable GPU acceleration

    Returns
    -------
    X_transformed : ndarray
        EAT-transformed methylation data
    T : ndarray
        EAT distortion vector
    """
    T = compute_eat_T(
        alpha_H, beta_H, alpha_C, beta_C,
        gamma=gamma, clip_T=clip_T, eps=eps,
        low_tau_threshold=low_tau_threshold,
        use_loggamma=use_loggamma, use_gpu=use_gpu
    )

    X_transformed = apply_eat_transform(
        X, T, norm=norm, eps=eps, use_gpu=use_gpu
    )

    return X_transformed, T


def validate_eat_methylation_data(X: np.ndarray) -> None:
    """Validate methylation data for EAT transformation."""
    if not isinstance(X, np.ndarray):
        raise TypeError(f"Methylation data must be ndarray, got {type(X)}")

    if X.ndim != 2:
        raise ValueError(f"Methylation data must be 2D, got shape {X.shape}")

    if not np.issubdtype(X.dtype, np.floating):
        raise TypeError(f"Methylation data must be floating point, got {X.dtype}")

    # Check value range (should be approximately [0,1])
    if np.any((X < -0.1) | (X > 1.1)):
        logger.warning(f"Methylation values outside expected [0,1] range: "
                      f"min={X.min():.3f}, max={X.max():.3f}")


# Self-test function
def _test_eat_transform():
    """Basic self-test for EAT transformation."""
    logger.info("Testing EAT transformation...")

    # Generate synthetic data
    rng = np.random.default_rng(42)
    L, N = 1000, 50  # loci, samples

    # Fake centroids: Healthy tighter/symmetric, Cancer shifted/skewed
    alpha_H = rng.uniform(5.0, 15.0, size=L)
    beta_H = rng.uniform(5.0, 15.0, size=L)
    alpha_C = alpha_H + rng.normal(0.5, 0.6, size=L)
    beta_C = beta_H + rng.normal(-0.3, 0.6, size=L)
    alpha_C = np.clip(alpha_C, 0.5, None)
    beta_C = np.clip(beta_C, 0.5, None)

    # Generate synthetic methylation data
    mu_H = beta_mean(alpha_H, beta_H)
    mu_C = beta_mean(alpha_C, beta_C)
    X_healthy = np.clip(mu_H[:, None] + rng.normal(0, 0.03, size=(L, N//2)), 0, 1)
    X_cancer = np.clip(mu_C[:, None] + rng.normal(0, 0.05, size=(L, N//2)), 0, 1)
    X = np.concatenate([X_healthy, X_cancer], axis=1)  # [L, N]

    # Apply EAT transformation
    X_eat, T = eat_transform_from_betas(
        X, alpha_H, beta_H, alpha_C, beta_C,
        gamma=1.0, norm=None, clip_T=10.0, use_gpu=False
    )

    # Basic validation
    assert T.shape == (L,), f"T shape {T.shape} != {(L,)}"
    assert X_eat.shape == X.shape, f"X_eat shape {X_eat.shape} != X shape {X.shape}"
    assert np.isfinite(T).all(), "T contains non-finite values"
    assert np.isfinite(X_eat).all(), "X_eat contains non-finite values"

    # Check that transformation changes the data
    raw_var = X.var(axis=0).mean()
    eat_var = X_eat.var(axis=0).mean()
    assert abs(eat_var - raw_var) > 1e-6, "EAT transformation should change variance"

    logger.info(f"✅ EAT test passed: raw_var={raw_var:.6f}, eat_var={eat_var:.6f}")
    return True


if __name__ == "__main__":
    _test_eat_transform()
