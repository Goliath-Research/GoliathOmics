"""Unit tests for the pure statistical kernels behind genome-wide derived measures.

These functions carry the scientific logic (entropy, weighted fractions,
adjacent-CpG disagreement, PMD load) and are exercised here without any H5 I/O.
"""

from __future__ import annotations

import math

import numpy as np

from methyl_derived_measures.core.genome_measures import (
    _adjacent_disagreement_fraction,
    _binary_entropy_mean,
    _pmd_load_fraction,
    _weighted_fraction,
)


def test_binary_entropy_is_one_bit_at_beta_half() -> None:
    values = np.array([0.5, 0.5, 0.5])
    weights = np.array([1.0, 1.0, 1.0])
    assert _binary_entropy_mean(values, weights) == 1.0


def test_binary_entropy_near_zero_for_extreme_beta() -> None:
    values = np.array([0.0, 1.0])
    weights = np.array([1.0, 1.0])
    # Clipped to eps, so entropy is small but finite and >= 0.
    result = _binary_entropy_mean(values, weights)
    assert 0.0 <= result < 1e-6


def test_binary_entropy_empty_and_zero_weights_return_nan() -> None:
    assert math.isnan(_binary_entropy_mean(np.array([]), np.array([])))
    assert math.isnan(_binary_entropy_mean(np.array([0.5]), np.array([0.0])))


def test_weighted_fraction_counts_weight_in_range() -> None:
    values = np.array([0.1, 0.3, 0.8])
    weights = np.array([1.0, 2.0, 1.0])
    # Only 0.3 falls in [0.25, 0.75]; its weight is 2 of total 4.
    assert _weighted_fraction(values, weights, 0.25, 0.75) == 0.5


def test_weighted_fraction_zero_weight_returns_nan() -> None:
    assert math.isnan(_weighted_fraction(np.array([0.5]), np.array([0.0]), 0.0, 1.0))


def test_adjacent_disagreement_fraction_on_neighboring_positions() -> None:
    positions = np.array([10, 11, 12])
    values = np.array([0.1, 0.9, 0.95])
    coverage = np.array([5, 5, 5])
    # Pair (0.1, 0.9): |diff|=0.8 >= 0.25 -> disagree.
    # Pair (0.9, 0.95): |diff|=0.05 < 0.25 -> agree. Fraction = 0.5.
    frac = _adjacent_disagreement_fraction(
        positions, values, coverage, min_coverage=1, threshold=0.25
    )
    assert frac == 0.5


def test_adjacent_disagreement_requires_two_covered_sites() -> None:
    positions = np.array([10, 11])
    values = np.array([0.1, 0.9])
    coverage = np.array([5, 0])  # second site below min_coverage
    assert math.isnan(
        _adjacent_disagreement_fraction(
            positions, values, coverage, min_coverage=1, threshold=0.25
        )
    )


def test_pmd_load_fraction_detects_hypomethylated_windows() -> None:
    # A broad, densely sampled hypomethylated region -> load approaches 1.0.
    positions = np.arange(0, 300_000, 1000)
    values = np.full(positions.shape, 0.1)
    coverage = np.full(positions.shape, 5.0)
    load = _pmd_load_fraction(
        positions,
        values,
        coverage,
        min_coverage=1,
        window_bp=100_000,
        step_bp=50_000,
        beta_threshold=0.3,
    )
    assert load == 1.0


def test_pmd_load_fraction_zero_for_methylated_genome() -> None:
    positions = np.arange(0, 300_000, 1000)
    values = np.full(positions.shape, 0.8)
    coverage = np.full(positions.shape, 5.0)
    load = _pmd_load_fraction(
        positions,
        values,
        coverage,
        min_coverage=1,
        window_bp=100_000,
        step_bp=50_000,
        beta_threshold=0.3,
    )
    assert load == 0.0
