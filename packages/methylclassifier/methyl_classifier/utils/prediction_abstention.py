"""
Post-hoc abstention when too few DMP positions are observed for a sample.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


def apply_min_observed_dmp_abstention(
    probabilities: np.ndarray,
    availability_mask: Optional[np.ndarray],
    min_fraction: float,
    n_classes: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    After scoring/calibration: samples with observed-DMP fraction below ``min_fraction``
    get uniform class probabilities and prediction index -1 (``predicted_class`` = abstain in CSV).

    Args:
        probabilities: Shape (n_samples, n_classes).
        availability_mask: Boolean (n_samples, n_dmps) or None.
        min_fraction: Minimum fraction of DMP columns that must be available (0 disables).
        n_classes: Number of classes (for uniform distribution).

    Returns:
        (probabilities_out, predictions, abstain_flags)
    """
    prob = np.asarray(probabilities, dtype=np.float64, copy=True)
    n = prob.shape[0]
    abst = np.zeros(n, dtype=bool)
    pred = np.argmax(prob, axis=1)

    if min_fraction <= 0.0 or availability_mask is None:
        return prob, pred, abst

    mask = np.asarray(availability_mask, dtype=bool)
    if mask.ndim != 2 or mask.shape[0] != n:
        raise ValueError(
            f"availability_mask shape {getattr(mask, 'shape', None)} incompatible with "
            f"probabilities rows {n}"
        )
    n_dmps = mask.shape[1]
    if n_dmps == 0:
        return prob, pred, abst

    frac = np.sum(mask, axis=1).astype(np.float64) / float(n_dmps)
    abst = frac < float(min_fraction)
    if np.any(abst) and n_classes > 0:
        uni = np.full(n_classes, 1.0 / float(n_classes), dtype=np.float64)
        prob[abst, :] = uni
        pred = np.argmax(prob, axis=1)
        pred = np.where(abst, -1, pred)
    return prob, pred, abst