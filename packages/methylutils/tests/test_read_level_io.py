"""Tests for read-level pattern sidecar I/O."""

from __future__ import annotations

import numpy as np
import pytest

from methyl_utils.core.read_level_io import (
    ReadLevelPatterns,
    load_read_level_patterns,
    write_read_level_patterns,
)


def test_read_level_patterns_round_trip(tmp_path):
    data = ReadLevelPatterns(
        context="CG",
        tile_size=2,
        tile_start_pos=np.array([100, 200], dtype=np.uint32),
        tile_cpg_positions=np.array([[100, 101], [200, 201]], dtype=np.uint32),
        tile_n_reads=np.array([10, 20], dtype=np.uint32),
        pattern_tile_id=np.array([0, 0, 1], dtype=np.uint32),
        pattern_id=np.array([0, 3, 1], dtype=np.uint16),
        pattern_count=np.array([4, 6, 20], dtype=np.uint32),
        min_tile_reads=1,
    )
    path = tmp_path / "1-CG.patterns.h5"
    write_read_level_patterns(path, data)
    loaded = load_read_level_patterns(path)
    assert loaded.context == "CG"
    assert loaded.tile_size == 2
    assert loaded.n_tiles == 2
    assert loaded.tile_histogram(0) == {0: 4, 3: 6}
    assert loaded.tile_histogram(1) == {1: 20}
