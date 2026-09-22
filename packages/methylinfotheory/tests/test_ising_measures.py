"""Tests for Ising-derived MML/NME/ESI/MSI measures."""

from __future__ import annotations

import numpy as np
import pytest

from methyl_infotheory.core.ising import build_state_design
from methyl_infotheory.core.ising_measures import compute_measures_from_prob


def test_nme_uniform_is_one():
    design = build_state_design(2, "nearest")
    prob = np.full((1, 4), 0.25, dtype=np.float64)
    m = compute_measures_from_prob(prob, design, xp=np)
    assert m["nme"][0] == pytest.approx(1.0, abs=0.01)


def test_nme_single_pattern_is_zero():
    design = build_state_design(2, "nearest")
    prob = np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    m = compute_measures_from_prob(prob, design, xp=np)
    assert m["nme"][0] == pytest.approx(0.0, abs=0.01)


def test_mml_matches_methylated_fraction():
    design = build_state_design(2, "nearest")
    # pattern 3 = both methylated -> mml=1
    prob = np.array([[0.0, 0.0, 0.0, 1.0]], dtype=np.float64)
    m = compute_measures_from_prob(prob, design, xp=np)
    assert m["mml"][0] == pytest.approx(1.0, abs=0.01)


def test_sensitivities_non_negative():
    design = build_state_design(2, "nearest")
    prob = np.array([[0.25, 0.25, 0.25, 0.25]], dtype=np.float64)
    m = compute_measures_from_prob(prob, design, xp=np)
    assert m["esi"][0] >= 0.0
    assert m["msi"][0] >= 0.0
