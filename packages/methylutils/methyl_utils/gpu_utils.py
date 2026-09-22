"""
GPU utility functions for methylation analysis.

This module provides common GPU/CPU backend utilities used across
different metric computation modules.
"""

import numpy as np

# GPU statistical functions (will be initialized when CuPy/scipy are available)
_gpu_trigamma = None
_gpu_chi2_sf_df2 = None
_gpu_norm_cdf = None


def _init_gpu_functions():
    """Initialize GPU statistical functions if CuPy is available."""
    global _gpu_trigamma, _gpu_chi2_sf_df2, _gpu_norm_cdf

    try:
        import cupy as cp
        import cupyx.scipy.special as sp

        def gpu_trigamma(x):
            # Use polygamma with n as an array for CuPy compatibility
            n_array = cp.ones_like(x, dtype=cp.int32)
            return sp.polygamma(n_array, x)

        def gpu_chi2_sf_df2(x):
            return cp.exp(-x / 2)

        def gpu_norm_cdf(x):
            return 0.5 * sp.erfc(-x / cp.sqrt(2.0))

        _gpu_trigamma = gpu_trigamma
        _gpu_chi2_sf_df2 = gpu_chi2_sf_df2
        _gpu_norm_cdf = gpu_norm_cdf

    except ImportError:
        # Fallback to CPU versions if CuPy not available
        def cpu_trigamma(x):
            # This would need scipy implementation
            raise NotImplementedError("GPU functions not available")

        _gpu_trigamma = cpu_trigamma
        _gpu_chi2_sf_df2 = cpu_trigamma
        _gpu_norm_cdf = cpu_trigamma


# Initialize on import
_init_gpu_functions()


# Public interface for GPU statistical functions
def gpu_trigamma(x):
    """GPU-compatible trigamma function."""
    if _gpu_trigamma is None:
        raise RuntimeError("GPU functions not initialized")
    result = _gpu_trigamma(x)
    # Ensure result is NumPy array for API consistency
    if hasattr(result, 'get'):  # CuPy array
        return result.get()
    return result


def gpu_chi2_sf_df2(x):
    """GPU-compatible chi-squared survival function for df=2."""
    if _gpu_chi2_sf_df2 is None:
        raise RuntimeError("GPU functions not initialized")
    result = _gpu_chi2_sf_df2(x)
    # Ensure result is NumPy array for API consistency
    if hasattr(result, 'get'):  # CuPy array
        return result.get()
    return result


def gpu_norm_cdf(x):
    """GPU-compatible normal cumulative distribution function."""
    if _gpu_norm_cdf is None:
        raise RuntimeError("GPU functions not initialized")
    result = _gpu_norm_cdf(x)
    # Ensure result is NumPy array for API consistency
    if hasattr(result, 'get'):  # CuPy array
        return result.get()
    return result


def _prepare_arrays_for_backend(arrays, calc, use_gpu, dtype=None):
    """
    Utility function to prepare arrays for GPU/CPU backend.

    Args:
        arrays: List of arrays to prepare
        calc: DistanceCalculator instance
        use_gpu: Whether to use GPU
        dtype: Target dtype (defaults to float32)

    Returns:
        Tuple of (prepared_arrays, backend_module)
    """
    if dtype is None:
        dtype = np.float32

    gpu_available = calc.gpu_available

    if use_gpu and gpu_available:
        backend = calc.cp
        prepared_arrays = [calc.cp.asarray(arr, dtype=dtype) for arr in arrays]
    else:
        backend = np
        prepared_arrays = [np.asarray(arr, dtype=dtype) for arr in arrays]

    return prepared_arrays, backend


def _ensure_cpu_output(result, calc, use_gpu):
    """
    Ensure output is converted back to CPU arrays for consistency.

    Args:
        result: Result to potentially convert
        calc: DistanceCalculator instance
        use_gpu: Whether GPU was used

    Returns:
        CPU array result
    """
    if use_gpu and calc.gpu_available:
        if isinstance(result, tuple):
            return tuple(calc.cp.asnumpy(r) for r in result)
        else:
            return calc.cp.asnumpy(result)
    else:
        return result


__all__ = [
    "gpu_trigamma",
    "gpu_chi2_sf_df2",
    "gpu_norm_cdf",
    "_prepare_arrays_for_backend",
    "_ensure_cpu_output"
]
