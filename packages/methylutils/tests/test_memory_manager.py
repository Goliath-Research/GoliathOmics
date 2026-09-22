"""Tests for the CPU-side branches of the shared MemoryManager.

GPU paths self-skip when CuPy is unavailable; these cover the pure branches
(chunk sizing math, dtype optimization, shared-memory arrays, stats).
"""

from __future__ import annotations

import numpy as np
import pytest

from methyl_utils.memory_manager import (
    MemoryManager,
    get_memory_manager,
    is_shared_memory_array,
)


def test_memory_requirements_centroid_vs_basic_sample():
    mm = MemoryManager()
    centroid = mm._calculate_memory_requirements(1_000_000, "centroid")
    basic = mm._calculate_memory_requirements(1_000_000, "basic_sample")
    # centroid stores more per position than a basic sample.
    assert centroid["bytes_per_position"] > basic["bytes_per_position"]
    assert centroid["total_with_overhead_mb"] > centroid["memory_mb"]


def test_memory_requirements_unknown_structure_raises():
    mm = MemoryManager()
    with pytest.raises(ValueError, match="Unknown data structure"):
        mm._calculate_memory_requirements(1000, "not_a_structure")


def test_optimal_chunk_size_bounds():
    mm = MemoryManager(gpu_memory_limit_gb=80.0)
    chunk = mm.calculate_optimal_chunk_size(50_000_000, "centroid")
    assert 10_000_000 <= chunk <= 50_000_000  # min floor .. total positions cap


def test_optimize_array_dtype_downcasts_float64():
    mm = MemoryManager()
    arr = np.ones(100, dtype=np.float64)
    out = mm.optimize_array_dtype(arr)
    assert out.dtype == np.float32


def test_optimize_array_dtype_small_int_to_uint16():
    mm = MemoryManager()
    arr = np.arange(0, 100, dtype=np.int64)
    out = mm.optimize_array_dtype(arr)
    assert out.dtype == np.uint16


def test_shared_memory_array_roundtrip():
    mm = MemoryManager()
    arr = mm.create_shared_memory_array((4, 4), "float32")
    assert arr.shape == (4, 4)
    arr[0, 0] = 3.5
    assert arr[0, 0] == 3.5
    # A plain ndarray is never reported as shared-memory-backed.
    assert is_shared_memory_array(np.zeros((4, 4), dtype="float32")) is False
    # Detection must track the independent backing signal (presence of a live
    # SharedMemory block), not merely echo the _is_shared_memory flag. When
    # shared memory is available the array carries a _shared_memory block and
    # must be detected as shared; the regular-array fallback carries neither.
    has_shared_block = hasattr(arr, "_shared_memory")
    assert is_shared_memory_array(arr) is has_shared_block
    mm.cleanup_shared_memory_array(arr)


def test_get_memory_usage_reports_system_metrics():
    mm = MemoryManager()
    usage = mm.get_memory_usage()
    assert usage["system_memory_mb"] > 0
    assert "gpu_memory_gb" in usage


def test_get_memory_manager_is_singleton():
    assert get_memory_manager() is get_memory_manager()


def test_reset_stats_clears_counters():
    mm = MemoryManager()
    mm.memory_stats["chunks_processed"] = 5
    mm.reset_stats()
    assert mm.memory_stats["chunks_processed"] == 0
