"""CPU vs GPU parity for Ising fit (skips when CuPy unavailable)."""

from __future__ import annotations

import os

import numpy as np
import pytest

from methyl_infotheory.core.ising import build_state_design, fit_ising_batch
from methyl_utils.array_backend import get_array_module
from methyl_utils.gpu_detection import is_gpu_available


@pytest.mark.skipif(not is_gpu_available(), reason="CuPy GPU not available")
def test_ising_fit_cpu_gpu_parity():
    design = build_state_design(2, "nearest")
    hist_cpu = np.array([[40.0, 10.0, 10.0, 40.0]], dtype=np.float64)

    os.environ["METHYL_DISABLE_GPU"] = "1"
    try:
        fit_cpu = fit_ising_batch(hist_cpu, design, xp=np, max_iter=50, tol=1e-5, l2=1e-3)
    finally:
        os.environ.pop("METHYL_DISABLE_GPU", None)

    xp_gpu, used = get_array_module(True)
    if not used:
        pytest.skip("GPU backend not selected")
    hist_gpu = xp_gpu.asarray(hist_cpu)
    fit_gpu = fit_ising_batch(hist_gpu, design, xp=xp_gpu, max_iter=50, tol=1e-5, l2=1e-3)

    np.testing.assert_allclose(fit_cpu.prob, fit_gpu.prob, rtol=1e-4, atol=1e-4)
    np.testing.assert_allclose(fit_cpu.theta, fit_gpu.theta, rtol=1e-4, atol=1e-4)
