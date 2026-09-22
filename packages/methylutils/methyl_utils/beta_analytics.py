"""Beta-distribution analytics helpers shared across pipeline packages.

Historically these lived in a ``beta_analytics`` module that was removed while
``methyl_utils.__all__`` (and downstream imports such as
``methyl_cluster.centroid_manager``) still referenced it, breaking
``import methyl_cluster``. This module restores the small, well-defined helper
that is actually consumed: the Beta log probability density.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.special import betaln

cp: Any
try:  # pragma: no cover - GPU optional
    import cupy as _cupy  # type: ignore

    cp = _cupy
    _HAS_CUPY = True
except Exception:  # pragma: no cover - CPU-only environments
    cp = None
    _HAS_CUPY = False

__all__ = ["beta_log_pdf"]

# Clip support away from {0, 1} so log terms stay finite.
_EPS = 1e-12


def beta_log_pdf(
    x: Any,
    alpha: Any,
    beta: Any,
    *,
    use_gpu: bool = False,
) -> Any:
    """Elementwise natural log of the Beta(alpha, beta) probability density at ``x``.

    ``log f(x; a, b) = (a - 1) ln x + (b - 1) ln(1 - x) - ln B(a, b)``

    Args:
        x: Methylation fraction(s) in the open interval (0, 1).
        alpha: Beta distribution alpha parameter(s), broadcastable to ``x``.
        beta: Beta distribution beta parameter(s), broadcastable to ``x``.
        use_gpu: When True and CuPy is available, compute on the GPU.

    Returns:
        Array of log-density values matching the broadcast shape of the inputs.
    """
    if use_gpu and _HAS_CUPY:
        return _beta_log_pdf_gpu(x, alpha, beta)
    return _beta_log_pdf_cpu(x, alpha, beta)


def _beta_log_pdf_cpu(x: Any, alpha: Any, beta: Any) -> Any:
    x_arr = np.asarray(x, dtype=np.float64)
    a_arr = np.asarray(alpha, dtype=np.float64)
    b_arr = np.asarray(beta, dtype=np.float64)
    x_clipped = np.clip(x_arr, _EPS, 1.0 - _EPS)
    return (
        (a_arr - 1.0) * np.log(x_clipped)
        + (b_arr - 1.0) * np.log(1.0 - x_clipped)
        - betaln(a_arr, b_arr)
    )


def _beta_log_pdf_gpu(x: Any, alpha: Any, beta: Any) -> Any:  # pragma: no cover - GPU only
    xp: Any = cp
    try:
        from cupyx.scipy.special import betaln as _betaln  # type: ignore
    except Exception:  # older cupy without betaln
        from cupyx.scipy.special import gammaln as _gammaln  # type: ignore

        def _betaln(a: Any, b: Any) -> Any:
            return _gammaln(a) + _gammaln(b) - _gammaln(a + b)

    x_arr = xp.asarray(x, dtype=xp.float64)
    a_arr = xp.asarray(alpha, dtype=xp.float64)
    b_arr = xp.asarray(beta, dtype=xp.float64)
    x_clipped = xp.clip(x_arr, _EPS, 1.0 - _EPS)
    return (
        (a_arr - 1.0) * xp.log(x_clipped)
        + (b_arr - 1.0) * xp.log(1.0 - x_clipped)
        - _betaln(a_arr, b_arr)
    )
