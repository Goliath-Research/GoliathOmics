"""CPU/GPU numerical parity for ECDF-first statistical kernels."""

from __future__ import annotations

import os

import numpy as np
import pytest

from methyl_utils.array_backend import (
    get_array_module,
    prefer_gpu_default,
    resolve_array_backend,
)
from methyl_utils.mojo_numeric import mojo_numeric_available
from methyl_utils.core.distribution_views import ECDFView
from methyl_utils.ecdf_classifier import ECDFClassifier
from methyl_utils.statistical_tests import (
    ecdf_bhattacharyya_trapezoidal_from_bin_counts,
    ecdf_ks_statistic,
    mann_whitney_from_bin_counts,
)

requires_gpu = pytest.mark.skipif(
    not prefer_gpu_default(),
    reason="GPU not available or METHYL_DISABLE_GPU is set",
)

RTOL = 1e-4
ATOL = 1e-6


def _synthetic_bin_data(n_pos: int = 32, n_bins: int = 20, seed: int = 0):
    rng = np.random.default_rng(seed)
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1, dtype=np.float64)
    bc1 = rng.integers(1, 50, size=(n_pos, n_bins)).astype(np.float64)
    bc2 = rng.integers(1, 50, size=(n_pos, n_bins)).astype(np.float64)
    n1 = rng.integers(20, 200, size=n_pos).astype(np.float64)
    n2 = rng.integers(20, 200, size=n_pos).astype(np.float64)
    Sx1 = rng.uniform(0, n1, size=n_pos)
    Sx2 = rng.uniform(0, n2, size=n_pos)
    Sx2_1 = Sx1**2 + rng.uniform(0, n1, size=n_pos)
    Sx2_2 = Sx2**2 + rng.uniform(0, n2, size=n_pos)
    return bin_edges, bc1, bc2, n1, n2, Sx1, Sx2, Sx2_1, Sx2_2


@requires_gpu
def test_mann_whitney_cpu_gpu_parity():
    _, bc1, bc2, n1, n2, *_ = _synthetic_bin_data()
    cpu = mann_whitney_from_bin_counts(bc1, bc2, n1, n2, prefer_gpu=False)
    gpu = mann_whitney_from_bin_counts(bc1, bc2, n1, n2, prefer_gpu=True)
    for key in ("u_stat", "z_stat", "p_value", "var_u"):
        np.testing.assert_allclose(cpu[key], gpu[key], rtol=RTOL, atol=ATOL)


@requires_gpu
def test_bhattacharyya_cpu_gpu_parity():
    bin_edges, bc1, bc2, *_ = _synthetic_bin_data()
    cpu = ecdf_bhattacharyya_trapezoidal_from_bin_counts(
        bc1, bc2, bin_edges, prefer_gpu=False
    )
    gpu = ecdf_bhattacharyya_trapezoidal_from_bin_counts(
        bc1, bc2, bin_edges, prefer_gpu=True
    )
    np.testing.assert_allclose(cpu, gpu, rtol=RTOL, atol=ATOL)


@requires_gpu
def test_ecdf_cdf_batch_cpu_gpu_parity():
    bin_edges, bc1, bc2, _, _, Sx1, _, Sx2_1, _ = _synthetic_bin_data(n_pos=16)
    N1 = np.maximum(Sx1 + 1, 1.0)
    view1 = ECDFView(bin_edges, bc1, Sx1, N1, Sx2_1)
    view2 = ECDFView(bin_edges, bc2, Sx1 * 0.9, N1, Sx2_1)
    grid = np.linspace(0.0, 1.0, 128, dtype=np.float64)
    idx = np.arange(8, dtype=np.intp)
    os.environ["METHYL_DISABLE_GPU"] = "1"
    try:
        cpu = view1._cdf_batch(idx, grid)
    finally:
        os.environ.pop("METHYL_DISABLE_GPU", None)
    gpu = view1._cdf_batch(idx, grid)
    np.testing.assert_allclose(cpu, gpu, rtol=RTOL, atol=ATOL)
    ks_cpu = ecdf_ks_statistic(view1, view2, idx, grid_size=128)
    ks_gpu = ecdf_ks_statistic(view1, view2, idx, grid_size=128)
    np.testing.assert_allclose(ks_cpu, ks_gpu, rtol=RTOL, atol=ATOL)


def test_ecdf_classifier_scoring_stable():
    bin_edges, bc1, bc2, *_ = _synthetic_bin_data(n_pos=8, n_bins=10)
    positions = np.arange(bc1.shape[0], dtype=np.uint32)
    directions = np.ones(bc1.shape[0], dtype=np.int8)
    clf = ECDFClassifier(
        positions=positions,
        bin_edges=bin_edges,
        bin_counts_c1=bc1,
        bin_counts_c2=bc2,
        weights=np.ones(bc1.shape[0], dtype=np.float64),
        directions=directions,
    )
    X = np.linspace(0.05, 0.95, 8 * 4).reshape(4, 8)
    log1, log2, _ = clf.compute_log_pdf_matrices(X)
    assert np.all(np.isfinite(log1))
    assert np.all(np.isfinite(log2))


def test_mojo_scatter_matches_numpy_add_at():
    if not mojo_numeric_available():
        pytest.skip("mojo-align numeric/ not on this host")
    from methyl_utils import mojo_numeric

    acc = np.zeros(5, dtype=np.uint32)
    idx = np.array([0, 0, 3], dtype=np.intp)
    vals = np.array([2, 3, 4], dtype=np.uint32)
    expect = np.zeros(5, dtype=np.uint32)
    np.add.at(expect, idx, vals)
    mojo_numeric.scatter_add_u32(acc, idx, vals)
    np.testing.assert_array_equal(acc, expect)
    name, xp, used = resolve_array_backend(prefer_gpu=True, gpu_backend="mojo")
    assert name == "mojo"
    assert xp is np
    assert used is True


def test_get_array_module_respects_disable_env():
    os.environ["METHYL_DISABLE_GPU"] = "1"
    try:
        import numpy as np_mod

        xp, used = get_array_module(prefer_gpu=True)
        assert xp is np_mod
        assert used is False
    finally:
        os.environ.pop("METHYL_DISABLE_GPU", None)


def test_ecdf_view_overlap_matches_pdf_grid_length():
    """Regression: trapz abscissa must match _pdf_batch interval-midpoint output."""
    bin_edges, bc1, bc2, _, _, Sx1, _, Sx2_1, _ = _synthetic_bin_data(n_pos=4)
    N1 = np.maximum(Sx1 + 1, 1.0)
    view1 = ECDFView(bin_edges, bc1, Sx1, N1, Sx2_1)
    view2 = ECDFView(bin_edges, bc2, Sx1 * 0.8, N1, Sx2_1)
    overlap = view1.overlap(view2)
    assert overlap.shape == (4,)
    assert np.all(np.isfinite(overlap))
    assert np.all((overlap >= 0.0) & (overlap <= 1.0))


def test_mann_whitney_z_stat_preserves_sign():
    """Regression: z_stat must be signed, not abs(z)."""
    bc1 = np.zeros((1, 10), dtype=np.float64)
    bc2 = np.zeros((1, 10), dtype=np.float64)
    bc1[0, 0] = 50.0
    bc2[0, -1] = 50.0
    n1 = np.array([100.0])
    n2 = np.array([100.0])
    out = mann_whitney_from_bin_counts(bc1, bc2, n1, n2, prefer_gpu=False)
    assert out["z_stat"][0] < 0.0
    assert out["p_value"][0] < 1.0
