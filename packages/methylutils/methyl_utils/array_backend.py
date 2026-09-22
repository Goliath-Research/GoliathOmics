"""
Unified NumPy / CuPy / Mojo array backend for MethylPipeline numeric kernels.

When ``gpu_backend`` is unset, prefer CuPy if available unless METHYL_DISABLE_GPU
is set or prefer_gpu=False. Explicit ``gpu_backend=mojo`` uses mojo-align numeric
kernels (fail-closed; never silent CuPy fallback).
"""

from __future__ import annotations

import os
from typing import Any, Optional, Tuple

import numpy as np

from .gpu_detection import is_gpu_available

ArrayModule = Any
ArrayLike = Any

_GPU_MODULE: Optional[ArrayModule] = None
_CPU_MODULE = np
_ALLOWED_GPU_BACKENDS = frozenset({"numpy", "cupy", "mojo"})


def gpu_disabled_by_env() -> bool:
    """True when METHYL_DISABLE_GPU is set to a truthy value."""
    val = os.environ.get("METHYL_DISABLE_GPU", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def prefer_gpu_default() -> bool:
    """Whether numeric kernels should attempt GPU acceleration."""
    return is_gpu_available() and not gpu_disabled_by_env()


def get_array_module(prefer_gpu: Optional[bool] = None) -> Tuple[ArrayModule, bool]:
    """
    Return (xp, used_gpu) where xp is cupy or numpy.

    Args:
        prefer_gpu: None = auto (GPU if available and not disabled), False = force CPU.
    """
    global _GPU_MODULE
    if gpu_disabled_by_env():
        return _CPU_MODULE, False
    use_gpu = prefer_gpu_default() if prefer_gpu is None else bool(prefer_gpu)
    if not use_gpu:
        return _CPU_MODULE, False
    if _GPU_MODULE is None:
        try:
            import cupy as cp  # type: ignore

            if cp.is_available():
                _GPU_MODULE = cp
            else:
                return _CPU_MODULE, False
        except ImportError:
            return _CPU_MODULE, False
    return _GPU_MODULE, True


def normalize_gpu_backend(value: Optional[str]) -> Optional[str]:
    """Return ``numpy``, ``cupy``, ``mojo``, or ``None`` (inherit / auto)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "none", "auto"}:
        return None
    if text not in _ALLOWED_GPU_BACKENDS:
        raise ValueError(
            "gpu_backend must be one of numpy, cupy, mojo "
            f"(got {value!r})"
        )
    return text


def resolve_array_backend(
    prefer_gpu: Optional[bool] = None,
    gpu_backend: Optional[str] = None,
) -> Tuple[str, ArrayModule, bool]:
    """Return ``(backend_name, xp, used_accelerator)``.

    * ``gpu_backend is None`` — current CuPy-or-NumPy auto.
    * ``numpy`` — host NumPy.
    * ``cupy`` — CuPy required (fail if missing).
    * ``mojo`` — mojo-align ``numeric/`` required (fail if missing; no CuPy fallback).

    ``METHYL_DISABLE_GPU`` forces NumPy for every backend.
    ``prefer_gpu=False`` forces NumPy even when ``gpu_backend`` is set.
    """
    name = normalize_gpu_backend(gpu_backend)
    want = prefer_gpu_default() if prefer_gpu is None else bool(prefer_gpu)
    if gpu_disabled_by_env() or not want:
        return "numpy", _CPU_MODULE, False
    if name is None:
        xp, used = get_array_module(prefer_gpu=True)
        return ("cupy" if used else "numpy"), xp, used
    if name == "numpy":
        return "numpy", _CPU_MODULE, False
    if name == "cupy":
        try:
            import cupy as cp  # type: ignore

            if not cp.is_available():
                raise RuntimeError(
                    "gpu_backend=cupy but CuPy reports no CUDA device"
                )
        except ImportError as exc:
            raise RuntimeError("gpu_backend=cupy but CuPy is not installed") from exc
        global _GPU_MODULE
        _GPU_MODULE = cp
        return "cupy", cp, True
    from .mojo_numeric import require_mojo_numeric

    require_mojo_numeric()
    return "mojo", _CPU_MODULE, True


def to_cpu(x: ArrayLike) -> np.ndarray:
    """Convert array-like to host NumPy."""
    if x is None:
        return np.array([])
    if hasattr(x, "get"):
        return np.asarray(x.get())
    return np.asarray(x)


def to_device(x: ArrayLike, xp: ArrayModule) -> ArrayLike:
    """Copy or cast *x* to the target backend."""
    if xp is np or xp is _CPU_MODULE:
        return np.asarray(x)
    return xp.asarray(x)


def cdf_linear_interp_batch(
    xp: ArrayModule,
    cdf_at_edges: ArrayLike,
    bin_edges: ArrayLike,
    grid: ArrayLike,
) -> np.ndarray:
    """
    Monotone linear CDF interpolation on a uniform grid (vectorized over grid).

    cdf_at_edges: (n_positions, n_edges) CDF at histogram bin edges (includes 0 at left).
    bin_edges: (n_edges,) edge coordinates in [0, 1].
    grid: (n_grid,) query points in [0, 1].

    Returns host float64 array shape (n_positions, n_grid).
    """
    cdf_at_edges = xp.asarray(cdf_at_edges, dtype=xp.float64)
    bin_edges = xp.asarray(bin_edges, dtype=xp.float64)
    grid = xp.clip(xp.asarray(grid, dtype=xp.float64).ravel(), 0.0, 1.0)
    if cdf_at_edges.ndim == 1:
        cdf_at_edges = cdf_at_edges.reshape(1, -1)
    E = int(bin_edges.shape[0])
    if E < 2 or grid.size == 0:
        return to_cpu(xp.zeros((cdf_at_edges.shape[0], max(int(grid.size), 0)), dtype=xp.float64))
    idx = xp.searchsorted(bin_edges, grid, side="right") - 1
    idx = xp.clip(idx, 0, E - 2)
    widths = xp.maximum(bin_edges[idx + 1] - bin_edges[idx], 1e-20)
    t = xp.clip((grid - bin_edges[idx]) / widths, 0.0, 1.0)
    out = (1.0 - t) * cdf_at_edges[:, idx] + t * cdf_at_edges[:, idx + 1]
    return to_cpu(xp.clip(out, 0.0, 1.0))


def linear_interp_on_grid(
    xp: ArrayModule,
    query: ArrayLike,
    grid: ArrayLike,
    values: ArrayLike,
) -> np.ndarray:
    """Linear interpolation of *values* defined on *grid* at *query* points."""
    query = xp.clip(xp.asarray(query, dtype=xp.float64).ravel(), 0.0, 1.0)
    grid = xp.asarray(grid, dtype=xp.float64).ravel()
    values = xp.asarray(values, dtype=xp.float64).ravel()
    if grid.size < 2:
        return to_cpu(xp.zeros_like(query))
    idx = xp.searchsorted(grid, query, side="right") - 1
    idx = xp.clip(idx, 0, grid.size - 2)
    widths = xp.maximum(grid[idx + 1] - grid[idx], 1e-20)
    t = xp.clip((query - grid[idx]) / widths, 0.0, 1.0)
    return to_cpu((1.0 - t) * values[idx] + t * values[idx + 1])


def norm_sf(x: ArrayLike, *, prefer_gpu: Optional[bool] = None) -> np.ndarray:
    """Survival function of standard normal (host array)."""
    from scipy.stats import norm

    x_np = to_cpu(x).astype(np.float64, copy=False)
    xp, on_gpu = get_array_module(prefer_gpu)
    if on_gpu:
        try:
            import cupyx.scipy.special as cpspecial  # type: ignore

            x_g = xp.asarray(x_np, dtype=xp.float64)
            return to_cpu(0.5 * cpspecial.erfc(x_g / xp.sqrt(2.0)))
        except Exception:
            pass
    return norm.sf(x_np)


def kolmogorov_sf(x: ArrayLike, *, prefer_gpu: Optional[bool] = None) -> np.ndarray:
    """
    Survival function for Kolmogorov distribution (asymptotic two-sided KS).

    Matches scipy.stats.kstwobign.sf for large n; computed on CPU for portability.
    """
    from scipy.stats import kstwobign

    return kstwobign.sf(to_cpu(x).astype(np.float64, copy=False))


def get_special_backend(prefer_gpu: Optional[bool] = None):
    """
    Return (xp, digamma, polygamma_fn, betaln) compatible with DistanceCalculator.
    """
    xp, used = get_array_module(prefer_gpu)
    if used:
        try:
            from cupyx.scipy.special import (  # type: ignore
                betaln as cupy_betaln,
                digamma as cupy_digamma,
                polygamma as cupy_polygamma,
            )

            def polygamma_fn(n, x):
                return cupy_polygamma(
                    int(n) * xp.ones_like(x, dtype=xp.int32), x
                )

            return xp, cupy_digamma, polygamma_fn, cupy_betaln
        except ImportError:
            pass
    from scipy.special import betaln, digamma, polygamma

    return np, digamma, lambda n, x: polygamma(n, x), betaln


__all__ = [
    "gpu_disabled_by_env",
    "prefer_gpu_default",
    "get_array_module",
    "normalize_gpu_backend",
    "resolve_array_backend",
    "to_cpu",
    "to_device",
    "cdf_linear_interp_batch",
    "norm_sf",
    "kolmogorov_sf",
    "linear_interp_on_grid",
    "get_special_backend",
]
