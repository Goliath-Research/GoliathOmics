"""Unit tests for Houseman QP and seed basis loading."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from methyl_deconv.core.houseman import (
    default_seed_basis_path,
    houseman_qp,
    load_seed_basis,
)


def test_packaged_seed_basis_loads():
    basis = load_seed_basis(default_seed_basis_path())
    assert basis.M.shape[0] == 450
    assert basis.M.shape[1] == 6
    assert basis.cell_types == ("CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu")
    assert np.all((basis.M >= 0.0) & (basis.M <= 1.0))
    assert basis.provenance["coordinate_convention"] == "1-based"
    assert basis.probe_ids[0] == "cg08769189"
    assert basis.positions[0] == 55270205


def test_houseman_qp_recovers_known_mixture():
    rng = np.random.default_rng(0)
    n_m, n_ct = 80, 6
    M = rng.uniform(0.05, 0.95, size=(n_m, n_ct))
    true = np.array([0.05, 0.15, 0.10, 0.10, 0.20, 0.40], dtype=np.float64)
    y = M @ true + rng.normal(0.0, 0.01, size=n_m)
    y = np.clip(y, 0.0, 1.0)
    est = houseman_qp(M, y, use_gpu=False)
    assert est.shape == (n_ct,)
    assert pytest.approx(float(np.sum(est)), abs=1e-6) == 1.0
    assert np.all(est >= -1e-9)
    assert float(np.max(np.abs(est - true))) < 0.08


def test_houseman_qp_nonnegative_sum_to_one():
    M = np.eye(4, dtype=np.float64)
    y = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float64)
    est = houseman_qp(M, y, use_gpu=False)
    assert pytest.approx(float(np.sum(est)), abs=1e-6) == 1.0
    assert np.all(est >= -1e-9)


def test_gpu_path_matches_cpu_when_available():
    from methyl_utils.gpu_detection import is_gpu_available

    rng = np.random.default_rng(1)
    M = rng.uniform(0.0, 1.0, size=(40, 5))
    true = np.full(5, 0.2)
    y = M @ true
    cpu = houseman_qp(M, y, use_gpu=False)
    if not is_gpu_available():
        pytest.skip("GPU/CuPy not available")
    gpu = houseman_qp(M, y, use_gpu=True)
    np.testing.assert_allclose(cpu, gpu, atol=1e-5)
