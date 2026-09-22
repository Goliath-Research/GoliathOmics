"""Tests for genome-wide derived measures."""

from __future__ import annotations

import numpy as np

from methyl_derived_measures.core.genome_measures import (
    _adjacent_disagreement_fraction,
    _binary_entropy_mean,
    _pmd_load_fraction,
    _weighted_fraction,
)


def test_binary_entropy_and_weighted_fraction():
    vals = np.array([0.1, 0.9, 0.5], dtype=np.float64)
    w = np.array([1.0, 1.0, 1.0], dtype=np.float64)
    assert np.isfinite(_binary_entropy_mean(vals, w))
    assert 0.0 <= _weighted_fraction(vals, w, 0.0, 0.2) <= 1.0


def test_adjacent_disagreement_fraction():
    pos = np.array([10, 11, 12], dtype=np.uint32)
    vals = np.array([0.1, 0.9, 0.2], dtype=np.float64)
    cov = np.array([5, 5, 5], dtype=np.int64)
    frac = _adjacent_disagreement_fraction(
        pos, vals, cov, min_coverage=1, threshold=0.25
    )
    assert np.isfinite(frac)


def test_pmd_load_fraction_valid_mask_operator_precedence():
    """Regression: & binds tighter than >= without parentheses."""
    cov = np.array([5, 5, 0], dtype=np.int64)
    vals = np.array([0.1, np.nan, np.nan], dtype=np.float64)
    min_coverage = 1
    wrong = cov >= int(min_coverage) & np.isfinite(vals)
    right = (cov >= int(min_coverage)) & np.isfinite(vals)
    assert wrong.tolist() == [True, True, True]
    assert right.tolist() == [True, False, False]


def test_pmd_load_fraction_excludes_non_finite_betas():
    pos = np.array([1000, 2000, 3000], dtype=np.uint32)
    cov = np.array([10, 10, 10], dtype=np.int64)
    vals = np.array([0.1, np.nan, 0.1], dtype=np.float64)
    result = _pmd_load_fraction(
        pos,
        vals,
        cov,
        min_coverage=1,
        window_bp=1500,
        step_bp=500,
        beta_threshold=0.3,
    )
    assert result == 1.0
