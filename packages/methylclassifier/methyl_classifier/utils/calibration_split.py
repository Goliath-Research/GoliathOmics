"""
Stratified holdout mask for fitting isotonic calibration and chromosome stacking
on a training fraction per class (same spirit as MethylValidation MC splits).
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def stratified_calibration_fit_mask(
    labels: np.ndarray,
    train_fraction: float,
    seed: Optional[int] = None,
) -> np.ndarray:
    """
    Return a boolean length-n array: True = use row to fit calibration/stacking.

    Within each class, approximately ``train_fraction`` of samples (at least one when
    n>=1, and when n>=2 leaves at least one sample not in the fit set when possible).
    """
    y = np.asarray(labels).astype(int).ravel()
    n = len(y)
    if n == 0:
        return np.zeros(0, dtype=bool)
    rng = np.random.default_rng(seed)
    fit = np.zeros(n, dtype=bool)
    for c in np.unique(y):
        idx = np.where(y == c)[0]
        rng.shuffle(idx)
        m = len(idx)
        if m == 0:
            continue
        n_take = max(1, int(np.floor(m * float(train_fraction))))
        if m >= 2 and n_take >= m:
            n_take = m - 1
        fit[idx[:n_take]] = True
    if int(fit.sum()) < 2:
        return np.ones(n, dtype=bool)
    return fit
