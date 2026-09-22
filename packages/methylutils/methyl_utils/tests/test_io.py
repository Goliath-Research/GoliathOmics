# tests/test_io.py
"""Tests for methyl_utils.core.io, including position-subset loading (binary search in H5)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import h5py
import numpy as np
import pytest

from methyl_utils.core.io import (
    _indices_for_positions,
    _indices_for_positions_h5,
    _row_range_for_bp,
    iter_bp_shards,
    load_from_h5,
)
from methyl_utils.core.methyl_frame import MethylCentroid, MethylSample


def _write_sample_h5(path: Path, pos: np.ndarray, mC=None, uC=None, tnc=None) -> None:
    n = len(pos)
    if mC is None:
        mC = np.arange(1, n + 1, dtype=np.uint32)
    if uC is None:
        uC = np.arange(n, 0, -1, dtype=np.uint32)
    if tnc is None:
        tnc = np.zeros(n, dtype=np.uint8)
    with h5py.File(path, "w") as f:
        g = f.create_group("methylation_data")
        g.create_dataset("pos", data=np.asarray(pos, dtype=np.uint32))
        g.create_dataset("mC", data=np.asarray(mC, dtype=np.uint32))
        g.create_dataset("uC", data=np.asarray(uC, dtype=np.uint32))
        g.create_dataset("tnc", data=np.asarray(tnc, dtype=np.uint8))


def test_indices_for_positions_h5_matches_full_load():
    """_indices_for_positions_h5 returns same indices as _indices_for_positions(loaded_pos, positions)."""
    # Sorted pos in file (e.g. 100 positions)
    pos_full = np.arange(1000, 1100, dtype=np.uint32)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with h5py.File(path, "w") as f:
            g = f.create_group("methylation_data")
            g.create_dataset("pos", data=pos_full)
            g.create_dataset("mC", data=np.zeros(len(pos_full), dtype=np.uint32))
            g.create_dataset("uC", data=np.ones(len(pos_full), dtype=np.uint32))
            g.create_dataset("tnc", data=np.zeros(len(pos_full), dtype=np.uint8))

        with h5py.File(path, "r") as f:
            pos_dset = f["methylation_data"]["pos"]
            # Subset of positions + one not in file (999)
            positions = np.array([1005, 1020, 1030, 999, 1080], dtype=np.uint32)
            idx_h5 = _indices_for_positions_h5(pos_dset, positions)

        # Compare to in-memory reference
        pos_arr = np.asarray(pos_full, dtype=np.uint32)
        idx_ref = _indices_for_positions(pos_arr, positions)
        np.testing.assert_array_equal(idx_h5, idx_ref)
        # 999 is not in file, so we get 4 indices (for 1005, 1020, 1030, 1080)
        assert len(idx_h5) == 4
    finally:
        path.unlink(missing_ok=True)


def test_indices_for_positions_h5_empty_positions():
    """_indices_for_positions_h5 with empty positions returns empty array."""
    pos_full = np.array([1, 2, 3], dtype=np.uint32)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with h5py.File(path, "w") as f:
            g = f.create_group("methylation_data")
            g.create_dataset("pos", data=pos_full)
            g.create_dataset("mC", data=np.zeros(3, dtype=np.uint32))
            g.create_dataset("uC", data=np.ones(3, dtype=np.uint32))
            g.create_dataset("tnc", data=np.zeros(3, dtype=np.uint8))
        with h5py.File(path, "r") as f:
            pos_dset = f["methylation_data"]["pos"]
            idx = _indices_for_positions_h5(pos_dset, np.array([], dtype=np.uint32))
        assert idx.shape == (0,)
        assert idx.dtype == np.int32
    finally:
        path.unlink(missing_ok=True)


def test_load_from_h5_positions_subset_equals_full_then_filter():
    """load_from_h5(path, positions=...) returns same data as loading full then filtering by positions."""
    pos_full = np.array([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], dtype=np.uint32)
    mC = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=np.uint32)
    uC = np.array([9, 8, 7, 6, 5, 4, 3, 2, 1, 0], dtype=np.uint32)
    tnc = np.zeros(10, dtype=np.uint8)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with h5py.File(path, "w") as f:
            g = f.create_group("methylation_data")
            g.create_dataset("pos", data=pos_full)
            g.create_dataset("mC", data=mC)
            g.create_dataset("uC", data=uC)
            g.create_dataset("tnc", data=tnc)

        positions = np.array([20, 50, 90], dtype=np.uint32)
        subset = load_from_h5(path, positions=positions)
        full = load_from_h5(path)

        # Subset should have only the requested positions that exist (all three exist)
        np.testing.assert_array_equal(np.sort(subset.pos.values), positions)
        # Values at those positions should match full
        for p in positions:
            idx_full = np.flatnonzero(full.pos.values == p)[0]
            idx_sub = np.flatnonzero(subset.pos.values == p)[0]
            assert subset.mC.values[idx_sub] == full.mC.values[idx_full]
            assert subset.uC.values[idx_sub] == full.uC.values[idx_full]
    finally:
        path.unlink(missing_ok=True)


def test_methylsample_load_from_h5_with_indices():
    """MethylSample.load_from_h5 supports indexed loads via shared API."""
    pos_full = np.array([10, 20, 30, 40], dtype=np.uint32)
    mC = np.array([1, 2, 3, 4], dtype=np.uint32)
    uC = np.array([9, 8, 7, 6], dtype=np.uint32)
    tnc = np.zeros(4, dtype=np.uint8)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        with h5py.File(path, "w") as f:
            g = f.create_group("methylation_data")
            g.create_dataset("pos", data=pos_full)
            g.create_dataset("mC", data=mC)
            g.create_dataset("uC", data=uC)
            g.create_dataset("tnc", data=tnc)

        loaded = MethylSample.load_from_h5(path, indices=np.array([0, 2], dtype=np.int32))
        np.testing.assert_array_equal(np.asarray(loaded.pos.values, dtype=np.uint32), np.array([10, 30], dtype=np.uint32))
        np.testing.assert_array_equal(np.asarray(loaded.mC.values, dtype=np.uint32), np.array([1, 3], dtype=np.uint32))
    finally:
        path.unlink(missing_ok=True)


def test_methylsample_lookup_at_positions_reference_order_and_coverage():
    """lookup_at_positions preserves reference order and applies coverage threshold."""
    pos = np.array([10, 20, 30], dtype=np.uint32)
    mC = np.array([5, 0, 2], dtype=np.uint32)
    uC = np.array([5, 1, 0], dtype=np.uint32)
    tnc = np.zeros(3, dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos=pos, mC=mC, uC=uC, tnc=tnc)

    query = np.array([20, 40, 10, 30], dtype=np.uint32)
    values, availability = sample.lookup_at_positions(query, min_coverage=2, missing_value=np.nan)

    # pos=20 has coverage 1 -> unavailable, pos=40 missing -> unavailable
    assert availability.tolist() == [False, False, True, True]
    # returned in query order
    assert np.isnan(values[0])
    assert np.isnan(values[1])
    assert values[2] == pytest.approx(0.5)
    assert values[3] == pytest.approx(1.0)


def test_methylcentroid_lookup_at_positions_uses_weighted_mean():
    """Centroid lookup uses Sm/(Sm+Su), not derived mC/coverage."""
    import pandas as pd

    pos = np.array([10, 20, 30], dtype=np.uint32)
    df = pd.DataFrame(
        {
            "pos": pos,
            "tnc": np.zeros(3, dtype=np.uint8),
            "N": np.array([2, 2, 2], dtype=np.uint32),
            "Sx": np.array([1.0, 0.5, 1.5], dtype=np.float32),
            "Sx2": np.array([0.5, 0.25, 0.75], dtype=np.float32),
            "Sm": np.array([6, 0, 8], dtype=np.uint32),
            "Su": np.array([4, 10, 2], dtype=np.uint32),
            "Sc2": np.array([100, 100, 100], dtype=np.uint32),
            "Swx2": np.array([0.36, 0.0, 0.64], dtype=np.float32),
        }
    )
    centroid = MethylCentroid(df)

    query = np.array([20, 40, 10, 30], dtype=np.uint32)
    values, availability = centroid.lookup_at_positions(query, min_coverage=5, missing_value=np.nan)

    assert availability.tolist() == [True, False, True, True]
    assert np.isnan(values[1])
    assert values[0] == pytest.approx(0.0)  # Sm=0, Su=10
    assert values[2] == pytest.approx(0.6)  # 6/(6+4)
    assert values[3] == pytest.approx(0.8)  # 8/(8+2)


def test_row_range_for_bp_matches_searchsorted():
    """_row_range_for_bp matches np.searchsorted on full pos (array and H5)."""
    pos_full = np.array([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], dtype=np.uint32)
    start_bp, end_bp = 25, 75
    lo_ref = int(np.searchsorted(pos_full, start_bp, side="left"))
    hi_ref = int(np.searchsorted(pos_full, end_bp, side="left"))
    assert _row_range_for_bp(pos_full, start_bp, end_bp) == (lo_ref, hi_ref)

    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_sample_h5(path, pos_full)
        with h5py.File(path, "r") as f:
            pos_dset = f["methylation_data"]["pos"]
            assert _row_range_for_bp(pos_dset, start_bp, end_bp) == (lo_ref, hi_ref)
            assert _row_range_for_bp(pos_dset, 0, 10) == (0, 0)
            assert _row_range_for_bp(pos_dset, 101, 200) == (10, 10)
            assert _row_range_for_bp(pos_dset, 10, 10) == (0, 0)
    finally:
        path.unlink(missing_ok=True)


def test_load_from_h5_bp_range_equals_full_then_filter():
    """load_from_h5(start_bp=, end_bp=) matches full load filtered by half-open range."""
    pos_full = np.array([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], dtype=np.uint32)
    mC = np.arange(1, 11, dtype=np.uint32)
    uC = np.arange(10, 0, -1, dtype=np.uint32)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_sample_h5(path, pos_full, mC=mC, uC=uC)
        start_bp, end_bp = 30, 80
        ranged = load_from_h5(path, start_bp=start_bp, end_bp=end_bp)
        full = load_from_h5(path)
        mask = (np.asarray(full.pos.values) >= start_bp) & (np.asarray(full.pos.values) < end_bp)
        np.testing.assert_array_equal(
            np.asarray(ranged.pos.values, dtype=np.uint32),
            np.asarray(full.pos.values, dtype=np.uint32)[mask],
        )
        np.testing.assert_array_equal(
            np.asarray(ranged.mC.values, dtype=np.uint32),
            np.asarray(full.mC.values, dtype=np.uint32)[mask],
        )
        # MethylSample.load_from_h5 wires the same kwargs
        via_cls = MethylSample.load_from_h5(path, start_bp=start_bp, end_bp=end_bp)
        np.testing.assert_array_equal(
            np.asarray(via_cls.pos.values, dtype=np.uint32),
            np.asarray(ranged.pos.values, dtype=np.uint32),
        )
    finally:
        path.unlink(missing_ok=True)


def test_load_from_h5_bp_range_empty_and_out_of_range():
    """Empty interior / out-of-range bp loads return an empty MethylSample."""
    pos_full = np.array([100, 200, 300], dtype=np.uint32)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_sample_h5(path, pos_full)
        empty_gap = load_from_h5(path, start_bp=101, end_bp=200)
        assert len(empty_gap) == 0
        empty_left = load_from_h5(path, start_bp=0, end_bp=50)
        assert len(empty_left) == 0
        empty_right = load_from_h5(path, start_bp=301, end_bp=400)
        assert len(empty_right) == 0
    finally:
        path.unlink(missing_ok=True)


def test_load_from_h5_bp_range_conflicts_with_positions():
    """Combining start_bp/end_bp with positions= or indices= raises ValueError."""
    pos_full = np.array([10, 20, 30], dtype=np.uint32)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_sample_h5(path, pos_full)
        with pytest.raises(ValueError, match="cannot be combined"):
            load_from_h5(path, positions=np.array([10], dtype=np.uint32), start_bp=0, end_bp=100)
        with pytest.raises(ValueError, match="cannot be combined"):
            load_from_h5(path, indices=np.array([0], dtype=np.int32), start_bp=0, end_bp=100)
        with pytest.raises(ValueError, match="end_bp"):
            load_from_h5(path, start_bp=50, end_bp=10)
    finally:
        path.unlink(missing_ok=True)


def test_iter_bp_shards_concatenation_equals_full():
    """iter_bp_shards concatenation equals full load (row order preserved)."""
    # Sparse positions across a wider genomic span
    pos_full = np.array([10, 25, 40, 55, 70, 100, 150, 200], dtype=np.uint32)
    mC = np.arange(1, 9, dtype=np.uint32)
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_sample_h5(path, pos_full, mC=mC)
        full = load_from_h5(path)
        shards = list(iter_bp_shards(path, shard_bp=50))
        assert len(shards) >= 2
        concat_pos = np.concatenate([np.asarray(s.pos.values, dtype=np.uint32) for s in shards])
        concat_mC = np.concatenate([np.asarray(s.mC.values, dtype=np.uint32) for s in shards])
        np.testing.assert_array_equal(concat_pos, np.asarray(full.pos.values, dtype=np.uint32))
        np.testing.assert_array_equal(concat_mC, np.asarray(full.mC.values, dtype=np.uint32))
        # Outer bounds
        bounded = list(iter_bp_shards(path, shard_bp=30, start_bp=40, end_bp=100))
        bounded_pos = np.concatenate([np.asarray(s.pos.values, dtype=np.uint32) for s in bounded])
        np.testing.assert_array_equal(bounded_pos, np.array([40, 55, 70], dtype=np.uint32))
    finally:
        path.unlink(missing_ok=True)


def test_iter_bp_shards_rejects_non_positive_shard_bp():
    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        _write_sample_h5(path, np.array([1, 2, 3], dtype=np.uint32))
        with pytest.raises(ValueError, match="shard_bp"):
            list(iter_bp_shards(path, shard_bp=0))
    finally:
        path.unlink(missing_ok=True)
