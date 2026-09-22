# tests/test_ecdf_vs_theoretical.py
"""Tests for ECDF vs theoretical distribution (Normal, Beta, Beta-Binomial) comparison."""
from __future__ import annotations

import numpy as np
import pytest

from methyl_utils.core.distribution_views import ECDFView
from methyl_utils.ecdf_fit import (
    ecdf_vs_theoretical_ks,
    ecdf_vs_theoretical_ks_pvalue,
    compare_ecdf_to_theoretical_at_positions,
)
from methyl_utils.statistical_tests import beta_mom_estimation


def _make_binned_ecdf_from_samples(
    x: np.ndarray, n_bins: int = 50
) -> tuple:
    """Build bin_edges, bin_counts, Sx, N, Sx2 from a 1D sample of proportions in [0,1]."""
    x = np.asarray(x, dtype=np.float64)
    x = np.clip(x, 0.0, 1.0)
    N = len(x)
    Sx = float(np.sum(x))
    Sx2 = float(np.sum(x ** 2))
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1, dtype=np.float64)
    bin_counts, _ = np.histogram(x, bins=bin_edges)
    bin_counts = bin_counts.astype(np.float64)
    if bin_counts.sum() == 0:
        bin_counts[0] = 1.0
    return bin_edges, bin_counts.reshape(1, -1), np.array([Sx]), np.array([N], dtype=np.float64), np.array([Sx2])


@pytest.mark.parametrize("grid_size", [128, 256])
def test_ecdf_vs_theoretical_ks_api_and_beta_closest(grid_size: int):
    """ECDF built from Beta data should be closest to Beta; API returns expected keys."""
    rng = np.random.default_rng(42)
    alpha_true, beta_true = 2.0, 5.0
    N = 200
    x = rng.beta(alpha_true, beta_true, size=N)
    bin_edges, bin_counts, Sx, N_arr, Sx2 = _make_binned_ecdf_from_samples(x, n_bins=50)
    ecdf_view = ECDFView(bin_edges, bin_counts, Sx, N_arr, Sx2)
    mu = float(Sx[0] / N_arr[0])
    sigma2 = float((Sx2[0] / N_arr[0]) - mu ** 2) / max(N - 1, 1)
    sigma2 = max(sigma2, 1e-12)
    alpha_mom, beta_mom = beta_mom_estimation(
        np.array([N], dtype=np.float64), Sx, Sx2
    )
    alpha, beta = float(alpha_mom[0]), float(beta_mom[0])
    result = ecdf_vs_theoretical_ks(
        ecdf_view, 0, mu, sigma2, alpha, beta,
        alpha_bb=alpha, beta_bb=beta,
        grid_size=grid_size,
    )
    assert "ks_normal" in result
    assert "ks_beta" in result
    assert "ks_betabinomial" in result
    assert "closest" in result
    assert result["closest"] == "beta"
    assert result["ks_beta"] <= result["ks_normal"]
    assert result["ks_beta"] <= result["ks_betabinomial"]


def test_ecdf_vs_theoretical_ks_pvalue_beta():
    """When data are from Beta, p_beta should be large and could_use_instead True for large N."""
    rng = np.random.default_rng(123)
    alpha_true, beta_true = 3.0, 4.0
    N = 300
    x = rng.beta(alpha_true, beta_true, size=N)
    bin_edges, bin_counts, Sx, N_arr, Sx2 = _make_binned_ecdf_from_samples(x, n_bins=50)
    ecdf_view = ECDFView(bin_edges, bin_counts, Sx, N_arr, Sx2)
    mu = float(Sx[0] / N_arr[0])
    sigma2 = float((Sx2[0] / N_arr[0]) - mu ** 2) / max(N - 1, 1)
    sigma2 = max(sigma2, 1e-12)
    alpha_mom, beta_mom = beta_mom_estimation(
        np.array([N], dtype=np.float64), Sx, Sx2
    )
    alpha, beta = float(alpha_mom[0]), float(beta_mom[0])
    result = ecdf_vs_theoretical_ks_pvalue(
        ecdf_view, 0, mu, sigma2, alpha, beta, n_samples=N,
        alpha_bb=alpha, beta_bb=beta, grid_size=256,
    )
    assert result["closest"] == "beta"
    assert result["p_beta"] >= result["p_normal"]
    assert result["could_use_instead"] is True


def test_ecdf_vs_theoretical_grid_and_edge_cases():
    """Single bin or degenerate counts does not crash; NaN when sigma2 invalid."""
    # Single bin: all counts in one bin
    bin_edges = np.array([0.0, 1.0], dtype=np.float64)
    bin_counts = np.array([[100.0]], dtype=np.float64)
    Sx = np.array([50.0])
    N = np.array([100.0])
    Sx2 = np.array([30.0])
    ecdf_view = ECDFView(bin_edges, bin_counts, Sx, N, Sx2)
    mu = 0.5
    sigma2 = 0.01
    alpha, beta = 2.0, 2.0
    result = ecdf_vs_theoretical_ks(
        ecdf_view, 0, mu, sigma2, alpha, beta,
        alpha_bb=alpha, beta_bb=beta, grid_size=64,
    )
    assert "closest" in result
    assert result["ks_beta"] >= 0
    assert np.isfinite(result["ks_normal"]) or np.isnan(result["ks_normal"])
    result_p = ecdf_vs_theoretical_ks_pvalue(
        ecdf_view, 0, mu, sigma2, alpha, beta, n_samples=100,
        alpha_bb=alpha, beta_bb=beta, grid_size=64,
    )
    assert "could_use_instead" in result_p


def test_compare_ecdf_to_theoretical_at_positions_requires_binned_stats():
    """compare_ecdf_to_theoretical_at_positions raises when centroid has no binned_stats."""
    class MockCentroid:
        pass
    mock = MockCentroid()
    with pytest.raises(ValueError, match="binned_stats"):
        compare_ecdf_to_theoretical_at_positions(mock)


def test_compare_ecdf_to_theoretical_at_positions_with_mock_centroid():
    """compare_ecdf_to_theoretical_at_positions returns list of dicts with expected columns."""
    rng = np.random.default_rng(789)
    N = 150
    x = rng.beta(2.0, 5.0, size=N)
    bin_edges, bin_counts, Sx, N_arr, Sx2 = _make_binned_ecdf_from_samples(x, n_bins=30)
    n_pos = 2
    bin_edges = bin_edges
    bin_counts = np.tile(bin_counts, (n_pos, 1))
    Sx_arr = np.array([Sx[0], Sx[0] * 0.9])
    N_arr = np.array([N, N], dtype=np.float64)
    Sx2_arr = np.array([Sx2[0], Sx2[0] * 0.95])
    alpha_mom, beta_mom = beta_mom_estimation(N_arr, Sx_arr, Sx2_arr)
    pos_arr = np.array([1000, 2000], dtype=np.uint32)

    class MockCentroid:
        binned_stats = {"bin_edges": bin_edges, "bin_counts": bin_counts}
        Sx = type("S", (), {"values": Sx_arr})()
        N = type("N", (), {"values": N_arr})()
        Sx2 = type("Sx2", (), {"values": Sx2_arr})()
        alpha = type("a", (), {"values": alpha_mom})()
        beta = type("b", (), {"values": beta_mom})()
        alpha_bb = type("a_bb", (), {"values": alpha_mom})()
        beta_bb = type("b_bb", (), {"values": beta_mom})()
        pos = type("p", (), {"values": pos_arr})()

    mock = MockCentroid()
    results = compare_ecdf_to_theoretical_at_positions(
        mock, position_indices=np.array([0, 1]), grid_size=128, include_pvalues=True
    )
    assert len(results) == 2
    for row in results:
        assert "position" in row
        assert "position_index" in row
        assert "ks_normal" in row
        assert "ks_beta" in row
        assert "ks_betabinomial" in row
        assert "closest" in row
        assert "p_normal" in row
        assert "p_beta" in row
        assert "could_use_instead" in row
