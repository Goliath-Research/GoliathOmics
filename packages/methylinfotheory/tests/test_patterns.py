"""Tests for read-level pattern statistics."""

from __future__ import annotations

import pytest

from methyl_infotheory.core.patterns import (
    pattern_epipolymorphism,
    pattern_pdr,
    pattern_shannon_entropy,
)


def test_pattern_entropy_all_same():
    hist = {0: 10}
    assert pattern_shannon_entropy(hist, tile_size=2) == pytest.approx(0.0)


def test_pattern_entropy_uniform_two_patterns():
    hist = {0: 5, 3: 5}
    assert pattern_shannon_entropy(hist, tile_size=2) == pytest.approx(0.5)


def test_pattern_entropy_uniform_all_patterns():
    hist = {0: 1, 1: 1, 2: 1, 3: 1}
    assert pattern_shannon_entropy(hist, tile_size=2) == pytest.approx(1.0)


def test_pattern_epipolymorphism_uniform():
    hist = {0: 1, 1: 1, 2: 1, 3: 1}
    assert pattern_epipolymorphism(hist) == pytest.approx(0.75)


def test_pattern_pdr_discordant_fraction():
    hist = {0: 2, 3: 2, 1: 6}  # 6/10 discordant for k=2
    assert pattern_pdr(hist, tile_size=2) == pytest.approx(0.6)
