"""
Tests for MethylSample coverage capping (binomial thinning) and coverage outlier detection.
"""

import numpy as np
import pytest

from methyl_utils.core.methyl_frame import (
    MethylSample,
    compute_coverage_outlier_flags,
)


def test_cap_coverage_binomial_returns_self():
    """cap_coverage_binomial mutates in place and returns self."""
    pos = np.array([1, 2, 3], dtype=np.uint32)
    mC = np.array([50, 60, 70], dtype=np.uint32)
    uC = np.array([50, 40, 30], dtype=np.uint32)
    tnc = np.array([0, 0, 0], dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    out = sample.cap_coverage_binomial(n_cap=30, seed=42)
    assert out is sample


def test_cap_coverage_binomial_under_cap_unchanged():
    """Positions with coverage <= n_cap are unchanged."""
    pos = np.array([1, 2, 3], dtype=np.uint32)
    mC = np.array([5, 10, 15], dtype=np.uint32)
    uC = np.array([5, 10, 15], dtype=np.uint32)
    tnc = np.array([1, 2, 3], dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    sample.cap_coverage_binomial(n_cap=100, seed=42)
    np.testing.assert_array_equal(sample.mC.values, mC)
    np.testing.assert_array_equal(sample.uC.values, uC)
    np.testing.assert_array_equal(sample.pos.values, pos)
    np.testing.assert_array_equal(sample._df["tnc"].values, tnc)


def test_cap_coverage_binomial_over_cap_reduced():
    """Positions with coverage > n_cap get thinned (coverage reduced in expectation)."""
    pos = np.array([1, 2, 3], dtype=np.uint32)
    mC = np.array([50, 60, 70], dtype=np.uint32)
    uC = np.array([50, 40, 30], dtype=np.uint32)
    tnc = np.array([1, 2, 3], dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    n_cap = 30
    sample.cap_coverage_binomial(n_cap=n_cap, seed=42)
    cov = sample.get_coverage()
    # After binomial thinning, total coverage is random with mean n_cap; check it changed and is bounded
    assert np.max(cov) < 100, "Thinned coverage should be much lower than original 100"
    np.testing.assert_array_equal(sample.pos.values, pos)
    np.testing.assert_array_equal(sample._df["tnc"].values, tnc)


def test_cap_coverage_binomial_reproducibility():
    """Same seed produces same thinned counts."""
    pos = np.array([1, 2, 3, 4], dtype=np.uint32)
    mC = np.array([50, 60, 70, 80], dtype=np.uint32)
    uC = np.array([50, 40, 30, 20], dtype=np.uint32)
    tnc = np.array([1, 2, 3, 4], dtype=np.uint8)
    s1 = MethylSample.from_sample_data(pos.copy(), mC.copy(), uC.copy(), tnc.copy())
    s2 = MethylSample.from_sample_data(pos.copy(), mC.copy(), uC.copy(), tnc.copy())
    s1.cap_coverage_binomial(n_cap=30, seed=123)
    s2.cap_coverage_binomial(n_cap=30, seed=123)
    np.testing.assert_array_equal(s1.mC.values, s2.mC.values)
    np.testing.assert_array_equal(s1.uC.values, s2.uC.values)


def test_cap_coverage_binomial_n_cap_raises():
    """n_cap < 1 raises ValueError."""
    pos = np.array([1], dtype=np.uint32)
    mC = np.array([10], dtype=np.uint32)
    uC = np.array([10], dtype=np.uint32)
    tnc = np.array([0], dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    with pytest.raises(ValueError, match="n_cap must be >= 1"):
        sample.cap_coverage_binomial(n_cap=0, seed=42)
    with pytest.raises(ValueError, match="n_cap must be >= 1"):
        sample.cap_coverage_binomial(n_cap=-1, seed=42)


def test_median_coverage_small_sample():
    """median_coverage with <= max_positions uses all positions."""
    pos = np.arange(100, dtype=np.uint32)
    mC = np.full(100, 5, dtype=np.uint32)
    uC = np.full(100, 5, dtype=np.uint32)
    tnc = np.zeros(100, dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    assert sample.median_coverage(max_positions=100_000, seed=0) == 10.0


def test_median_coverage_sampled():
    """median_coverage with large sample uses subset (result in reasonable range)."""
    n = 200_000
    pos = np.arange(n, dtype=np.uint32)
    cov_vals = np.full(n, 20, dtype=np.uint32)
    mC = (cov_vals // 2).astype(np.uint32)
    uC = (cov_vals - mC).astype(np.uint32)
    tnc = np.zeros(n, dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    med = sample.median_coverage(max_positions=10_000, seed=7)
    assert 15 <= med <= 25


def test_mean_coverage():
    """mean_coverage returns mean of mC+uC."""
    pos = np.array([1, 2, 3], dtype=np.uint32)
    mC = np.array([10, 20, 30], dtype=np.uint32)
    uC = np.array([10, 20, 30], dtype=np.uint32)
    tnc = np.zeros(3, dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    assert sample.mean_coverage() == 40.0


def test_compute_coverage_outlier_flags_robust_z():
    """One high-coverage sample is flagged with robust_z."""
    def make(med_cov, n_pos=1000):
        pos = np.arange(n_pos, dtype=np.uint32)
        mC = np.full(n_pos, med_cov // 2, dtype=np.uint32)
        uC = np.full(n_pos, med_cov - med_cov // 2, dtype=np.uint32)
        tnc = np.zeros(n_pos, dtype=np.uint8)
        return MethylSample.from_sample_data(pos, mC, uC, tnc)
    samples = [make(20), make(22), make(25), make(200)]  # last is outlier
    flags = compute_coverage_outlier_flags(samples, method="robust_z", threshold=3.5, seed=0)
    assert len(flags) == 4
    assert flags[-1] is True
    assert sum(flags) == 1


def test_compute_coverage_outlier_flags_iqr():
    """One high-coverage sample is flagged with IQR."""
    def make(med_cov, n_pos=1000):
        pos = np.arange(n_pos, dtype=np.uint32)
        mC = np.full(n_pos, med_cov // 2, dtype=np.uint32)
        uC = np.full(n_pos, med_cov - med_cov // 2, dtype=np.uint32)
        tnc = np.zeros(n_pos, dtype=np.uint8)
        return MethylSample.from_sample_data(pos, mC, uC, tnc)
    samples = [make(20), make(21), make(22), make(100)]
    flags = compute_coverage_outlier_flags(samples, method="iqr", seed=0)
    assert len(flags) == 4
    assert flags[-1] is True


def test_compute_coverage_outlier_flags_empty():
    """Empty sample list returns empty flags."""
    assert compute_coverage_outlier_flags([]) == []


def test_compute_coverage_outlier_flags_mad_zero():
    """When MAD is 0 (all same summary), no sample is flagged."""
    def make(n_pos=500):
        pos = np.arange(n_pos, dtype=np.uint32)
        mC = np.full(n_pos, 10, dtype=np.uint32)
        uC = np.full(n_pos, 10, dtype=np.uint32)
        tnc = np.zeros(n_pos, dtype=np.uint8)
        return MethylSample.from_sample_data(pos, mC, uC, tnc)
    samples = [make(), make(), make()]
    flags = compute_coverage_outlier_flags(samples, method="robust_z", threshold=3.5, seed=0)
    assert flags == [False, False, False]


def test_compute_coverage_outlier_flags_bad_method():
    """Invalid method raises ValueError."""
    pos = np.arange(10, dtype=np.uint32)
    mC = np.ones(10, dtype=np.uint32)
    uC = np.ones(10, dtype=np.uint32)
    tnc = np.zeros(10, dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    with pytest.raises(ValueError, match="method must be"):
        compute_coverage_outlier_flags([sample], method="invalid")
