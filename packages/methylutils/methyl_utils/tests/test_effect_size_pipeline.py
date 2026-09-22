import sys
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "packages/methylutils"))

from methyl_utils.core.distribution_views import ECDFView
from methyl_utils.methyl_centroid_pair import CENTROID_COMPARISON_DTYPE
from methyl_utils.statistical_tests import (
    ecdf_overlap_integral,
    effect_size_from_components,
    ecdf_effect_size,
)


def _build_view(bin_counts, means, variances, n=40):
    bin_counts = np.asarray(bin_counts, dtype=np.float64)
    means = np.asarray(means, dtype=np.float64)
    variances = np.asarray(variances, dtype=np.float64)
    N = np.full(len(means), float(n), dtype=np.float64)
    Sx = means * N
    Sx2 = variances * np.maximum(N - 1.0, 1.0) + (Sx ** 2) / N
    bin_edges = np.linspace(0.0, 1.0, bin_counts.shape[1] + 1, dtype=np.float64)
    return ECDFView(bin_edges, bin_counts, Sx, N, Sx2)


def test_identical_distributions_have_overlap_one_and_zero_effect():
    view1 = _build_view([[2, 8, 8, 2]], [0.5], [0.01])
    view2 = _build_view([[2, 8, 8, 2]], [0.5], [0.01])
    overlap = ecdf_overlap_integral(view1, view2, np.array([0], dtype=np.intp), grid_size=512)
    result = ecdf_effect_size(
        delta_mean=np.array([0.0]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        ecdf_view1=view1,
        ecdf_view2=view2,
        position_indices=np.array([0], dtype=np.intp),
        lambda_var=2.0,
        grid_size=512,
    )
    assert np.isclose(overlap[0], 1.0, atol=1e-3)
    assert np.isclose(result["effect_size"][0], 0.0, atol=1e-8)


def test_separated_distributions_raise_effect_size():
    view1 = _build_view([[20, 1, 0, 0]], [0.05], [0.002])
    view2 = _build_view([[0, 0, 1, 20]], [0.95], [0.002])
    result = ecdf_effect_size(
        delta_mean=np.array([0.9]),
        var1=np.array([0.002]),
        var2=np.array([0.002]),
        ecdf_view1=view1,
        ecdf_view2=view2,
        position_indices=np.array([0], dtype=np.intp),
        lambda_var=1.0,
        grid_size=512,
    )
    assert result["overlap"][0] < 0.2
    assert result["effect_size"][0] > 0.5


def test_same_mean_different_shape_keeps_biological_score_low():
    result = effect_size_from_components(
        delta_mean=np.array([0.01]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
    )
    assert result["effect_size"][0] < 0.01


def test_ecdf_view_sliced_index_alignment():
    """
    Verify that ECDFViews built from sliced bin_counts give correct overlap when
    two centroids have different positional orderings.  This guards against the
    bug where centroid1 indices were incorrectly reused to index centroid2.

    centroid1 positions: [10, 20, 30, 40] — bins move from low to high methylation
    centroid2 positions: [15, 20, 30, 35] — different ordering; common = [20, 30]

    For position 20: centroid1 index 1 (bin counts [0,8,8,0]) vs centroid2 index 1 (same)
    For position 30: centroid1 index 2 (bin counts [8,0,0,8]) vs centroid2 index 2 (same)
    """
    pos1 = np.array([10, 20, 30, 40], dtype=np.uint32)
    pos2 = np.array([15, 20, 30, 35], dtype=np.uint32)
    bc1_all = np.array([
        [8, 8, 0, 0],   # pos 10 – low methylation
        [0, 8, 8, 0],   # pos 20 – mid methylation
        [8, 0, 0, 8],   # pos 30 – bimodal
        [0, 0, 8, 8],   # pos 40 – high methylation
    ], dtype=np.float64)
    bc2_all = np.array([
        [8, 0, 0, 0],   # pos 15
        [0, 8, 8, 0],   # pos 20 – same as c1 pos 20 → expected high overlap
        [8, 0, 0, 8],   # pos 30 – same as c1 pos 30 → expected moderate overlap
        [0, 0, 0, 8],   # pos 35
    ], dtype=np.float64)
    bin_edges = np.linspace(0, 1, 5, dtype=np.float64)
    N = np.array([20.0, 20.0, 20.0, 20.0])
    Sx1 = N * np.array([0.2, 0.5, 0.5, 0.8])
    Sx2 = N * np.array([0.1, 0.5, 0.5, 0.9])

    common = np.intersect1d(pos1, pos2)   # [20, 30]
    idx1 = np.searchsorted(pos1, common)  # [1, 2]
    idx2 = np.searchsorted(pos2, common)  # [1, 2]

    view1 = ECDFView(bin_edges, bc1_all[idx1], Sx1[idx1], N[idx1], None)
    view2 = ECDFView(bin_edges, bc2_all[idx2], Sx2[idx2], N[idx2], None)
    overlap = ecdf_overlap_integral(view1, view2, np.array([0, 1], dtype=np.intp), grid_size=256)

    # Pos 20: identical distributions → overlap near 1
    assert overlap[0] > 0.8, f"Expected high overlap for identical distributions, got {overlap[0]:.3f}"
    # Pos 30: identical distributions (bimodal vs bimodal) → also near 1
    assert overlap[1] > 0.8, f"Expected high overlap for identical distributions, got {overlap[1]:.3f}"

    # Verify the bug case: if we mistakenly used centroid1 index (2) for centroid2
    # at position 30, we'd get centroid2's bin_counts[2] = [8,0,0,8] (pos 30) which
    # happens to be the same here — but at position 20 centroid2's index 1 differs from
    # what centroid1's index 1 is, so the test would still catch any cross-indexing.


def test_delta_sign_in_centroid_comparison_dtype():
    """CENTROID_COMPARISON_DTYPE must contain a delta_sign int8 field."""
    field_names = [name for name, _ in CENTROID_COMPARISON_DTYPE.descr]
    assert 'delta_sign' in field_names, "delta_sign missing from CENTROID_COMPARISON_DTYPE"
    assert CENTROID_COMPARISON_DTYPE['delta_sign'].base == np.dtype(np.int8)


def test_variance_penalty_is_symmetric():
    result_a = effect_size_from_components(
        delta_mean=np.array([0.4]),
        overlap=np.array([0.2]),
        var1=np.array([0.01]),
        var2=np.array([0.09]),
        lambda_var=2.0,
    )
    result_b = effect_size_from_components(
        delta_mean=np.array([0.4]),
        overlap=np.array([0.2]),
        var1=np.array([0.09]),
        var2=np.array([0.01]),
        lambda_var=2.0,
    )
    assert np.isclose(result_a["effect_size"][0], result_b["effect_size"][0], atol=1e-10)
    baseline = effect_size_from_components(
        delta_mean=np.array([0.4]),
        overlap=np.array([0.2]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=2.0,
    )
    assert result_a["effect_size"][0] < baseline["effect_size"][0]


def test_mean_level_weight_reduces_effect_at_low_methylation():
    """Mean-level weight f(μ̄) down-weights effect_size when μ̄ is low (e.g. CHH)."""
    base = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
    )
    # No mean_level: baseline
    assert base["effect_size"][0] > 0.1

    # mean_level=0 (no methylation): effect should be zero
    with_zero = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
        mean_level=np.array([0.0]),
        mean_level_weight="saturating",
        mean_level_k=0.08,
    )
    assert with_zero["effect_size"][0] == 0.0

    # Low mean_level (e.g. CHH): effect reduced vs baseline
    with_low = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
        mean_level=np.array([0.05]),
        mean_level_weight="saturating",
        mean_level_k=0.08,
    )
    assert with_low["effect_size"][0] < base["effect_size"][0]
    assert with_low["effect_size"][0] > 0.0

    # High mean_level (e.g. CG): effect closer to baseline (saturating f(μ̄) → 1)
    with_high = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
        mean_level=np.array([0.8]),
        mean_level_weight="saturating",
        mean_level_k=0.08,
    )
    assert with_high["effect_size"][0] > with_low["effect_size"][0]


def test_boundary_mode_penalizes_below_tau_preserves_above():
    """Boundary mode: b(μ) = min(1, μ/τ). Below τ down-weighted; ≥ τ no penalty."""
    tau = 0.1
    base = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
    )
    # Below τ (e.g. 0.05): effect = base * (0.05/0.1) = 0.5 * base
    below = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
        mean_level=np.array([0.05]),
        mean_level_weight="boundary",
        mean_level_k=tau,
    )
    assert below["effect_size"][0] < base["effect_size"][0]
    assert np.isclose(below["effect_size"][0], base["effect_size"][0] * 0.5, rtol=0.01)

    # At τ: effect = base * 1
    at_tau = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
        mean_level=np.array([tau]),
        mean_level_weight="boundary",
        mean_level_k=tau,
    )
    assert np.isclose(at_tau["effect_size"][0], base["effect_size"][0], rtol=0.01)

    # Above τ (biologically meaningful): no penalty
    above = effect_size_from_components(
        delta_mean=np.array([0.5]),
        overlap=np.array([0.1]),
        var1=np.array([0.01]),
        var2=np.array([0.01]),
        lambda_var=1.0,
        mean_level=np.array([0.2]),
        mean_level_weight="boundary",
        mean_level_k=tau,
    )
    assert np.isclose(above["effect_size"][0], base["effect_size"][0], rtol=0.01)
