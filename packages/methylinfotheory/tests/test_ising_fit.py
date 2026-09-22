"""Tests for batched Ising fit."""

from __future__ import annotations

import numpy as np
import pytest

from methyl_infotheory.core.ising import build_state_design, fit_ising_batch


def test_ising_fit_low_entropy_for_peaked_distribution():
    design = build_state_design(2, "nearest")
    xp = np
    # All reads on pattern 0 (all hypo) -> low entropy
    hist = xp.array([[100.0, 0.0, 0.0, 0.0]], dtype=xp.float64)
    fit = fit_ising_batch(hist, design, xp=xp, max_iter=80, tol=1e-5, l2=1e-3)
    assert fit.prob.shape == (1, 4)
    assert fit.prob[0, 0] == pytest.approx(1.0, abs=0.05)


def test_ising_fit_high_entropy_for_uniform():
    design = build_state_design(2, "nearest")
    xp = np
    hist = xp.array([[25.0, 25.0, 25.0, 25.0]], dtype=xp.float64)
    fit = fit_ising_batch(hist, design, xp=xp, max_iter=80, tol=1e-5, l2=1e-3)
    ent = -np.sum(fit.prob[0] * np.log2(fit.prob[0] + 1e-12))
    assert ent == pytest.approx(2.0, abs=0.15)
