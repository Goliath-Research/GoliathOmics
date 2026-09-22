# methyl_utils/core/methyl_distribution_utils.py
"""
Shared edge-case handling for methylation distributions (0/1 methylation levels).
Use clip_beta_params_for_bounds in mean/overlap/PDF for Beta, Beta-Binomial, BMM.
"""
from __future__ import annotations

import numpy as np

DEFAULT_MIN_PARAM = 1e-6
DEFAULT_MAX_PARAM = 1e6


def clip_beta_params_for_bounds(
    alpha: np.ndarray,
    beta: np.ndarray,
    min_param: float = DEFAULT_MIN_PARAM,
    max_param: float = DEFAULT_MAX_PARAM,
) -> "tuple[np.ndarray, np.ndarray]":
    """
    Clip Beta parameters to [min_param, max_param] for safe mean/overlap/PDF.
    Handles edge cases where methylation is 0 (mC=0) or 1 (uC=0).
    """
    alpha = np.asarray(alpha, dtype=np.float64)
    beta = np.asarray(beta, dtype=np.float64)
    alpha = np.clip(np.where(np.isfinite(alpha), alpha, min_param), min_param, max_param)
    beta = np.clip(np.where(np.isfinite(beta), beta, min_param), min_param, max_param)
    return alpha, beta
