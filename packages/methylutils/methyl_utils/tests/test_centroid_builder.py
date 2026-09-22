# tests/test_centroid_builder.py
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

try:
    from hypothesis import given, settings, strategies as st
    from hypothesis.extra.numpy import arrays
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

from methyl_utils.core.centroid_builder import MethylCentroidBuilder, build_centroid
from methyl_utils.core.methyl_frame import MethylCentroid
from methyl_utils.core.io import load_from_h5  # assuming you have a minimal loader

try:
    from methyl_utils.gpu_detection import is_gpu_available
    HAS_GPU = is_gpu_available()
except ImportError:
    HAS_GPU = False


# --------------------------------------------------------------------------- #
# Helper: Create a tiny in-memory HDF5 sample (no external files needed)
# --------------------------------------------------------------------------- #
def create_temp_sample(positions: List[int], mC: List[int], uC: List[int], tnc: List[int]) -> Path:
    import h5py

    tmp = Path(tempfile.mkstemp(suffix=".h5")[1])

    with h5py.File(tmp, "w") as f:
        group = f.create_group("methylation_data")
        group.create_dataset("pos", data=np.array(positions, dtype=np.uint32))
        group.create_dataset("mC", data=np.array(mC, dtype=np.uint32))
        group.create_dataset("uC", data=np.array(uC, dtype=np.uint32))
        group.create_dataset("tnc", data=np.array(tnc, dtype=np.uint8))

    return tmp


# --------------------------------------------------------------------------- #
# Basic sanity checks
# --------------------------------------------------------------------------- #
def test_empty_builder_raises():
    builder = MethylCentroidBuilder()
    with pytest.raises(ValueError, match="No data accumulated"):
        builder.finalize()


def test_builder_rejects_non_positive_binned_stats_bins():
    with pytest.raises(ValueError, match="binned_stats_bins must be >= 1"):
        MethylCentroidBuilder(binned_stats_bins=0)


def test_single_sample_passes_through():
    positions = [100, 200, 300]
    mC = [5, 0, 10]
    uC = [0, 3, 2]
    tnc = [0, 1, 2]

    sample_path = create_temp_sample(positions, mC, uC, tnc)

    builder = MethylCentroidBuilder(min_coverage=1)
    builder.add_sample(sample_path)

    centroid = builder.finalize()

    assert isinstance(centroid, MethylCentroid)
    assert len(centroid) == 3
    assert centroid.N.to_list() == [1, 1, 1]
    np.testing.assert_array_equal(centroid.pos.values, positions)

    # Averaged counts should equal original (N=1)
    np.testing.assert_array_equal(centroid.mC.values, mC)
    np.testing.assert_array_equal(centroid.uC.values, uC)


def test_missing_residualize_apply_matches_explicit_none():
    """Shipped-pack path: unset hook equals residualize_apply=None."""
    sample = create_temp_sample([100, 200], [8, 2], [2, 8], [0, 0])
    unset = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    explicit = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    explicit.residualize_apply = None
    unset.add_sample(sample)
    explicit.add_sample(sample)
    a = unset.finalize()
    b = explicit.finalize()
    np.testing.assert_array_equal(np.asarray(a.Sx), np.asarray(b.Sx))
    np.testing.assert_array_equal(np.asarray(a.Sx2), np.asarray(b.Sx2))
    np.testing.assert_array_equal(np.asarray(a.mC), np.asarray(b.mC))
    np.testing.assert_array_equal(np.asarray(a.uC), np.asarray(b.uC))
    np.testing.assert_array_equal(a.binned_stats["bin_counts"], b.binned_stats["bin_counts"])


def test_residualize_apply_rewrites_sx_not_raw_counts():
    sample = create_temp_sample([100, 200], [8, 2], [2, 8], [0, 0])
    raw = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    adj = MethylCentroidBuilder(min_coverage=1, use_gpu=False)

    def bump(_path, _pos, mean):
        return np.clip(np.asarray(mean, dtype=np.float64) + 0.15, 1e-6, 1.0 - 1e-6)

    adj.residualize_apply = bump
    raw.add_sample(sample)
    adj.add_sample(sample)
    cr = raw.finalize()
    ca = adj.finalize()
    np.testing.assert_array_equal(np.asarray(cr.mC), np.asarray(ca.mC))
    np.testing.assert_array_equal(np.asarray(cr.uC), np.asarray(ca.uC))
    assert not np.allclose(np.asarray(cr.Sx), np.asarray(ca.Sx))


def test_residualize_apply_pos_matches_mean_when_second_sample_adds_positions():
    """OOB/new-position filtering must not pass unfiltered pos into residualize."""
    first = create_temp_sample([100, 200, 300], [4, 4, 4], [4, 4, 4], [0, 0, 0])
    second = create_temp_sample([100, 200, 300, 400, 50], [8, 0, 2, 1, 3], [2, 8, 2, 1, 1], [0, 0, 0, 0, 0])
    seen = []

    def capture(_path, pos, mean):
        seen.append((np.asarray(pos).shape, np.asarray(mean).shape))
        assert np.asarray(pos).shape == np.asarray(mean).shape
        return np.asarray(mean, dtype=np.float64)

    builder = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    builder.residualize_apply = capture
    builder.add_sample(first)
    builder.add_sample(second)
    builder.finalize()
    assert len(seen) == 2
    assert all(p == m for p, m in seen)


def test_incremental_add_sample_applies_residualize():
    """MethylCentroid.add_sample must use the same residualize hook as the builder."""
    s1 = create_temp_sample([10, 20], [8, 2], [2, 8], [0, 0])
    s2 = create_temp_sample([10, 20], [2, 8], [8, 2], [0, 0])

    def bump(_path, _pos, mean):
        return np.clip(np.asarray(mean, dtype=np.float64) + 0.2, 1e-6, 1.0 - 1e-6)

    streamed = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    streamed.residualize_apply = bump
    streamed.add_sample(s1)
    streamed.add_sample(s2)
    expected = streamed.finalize()

    first_only = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    first_only.residualize_apply = bump
    first_only.add_sample(s1)
    centroid = first_only.finalize()
    from methyl_utils.core.io import load_from_h5

    sample2 = load_from_h5(s2)
    mixed = centroid.add_sample(sample2, residualize_apply=bump, sample_path=str(s2))
    np.testing.assert_allclose(np.asarray(mixed.Sx), np.asarray(expected.Sx), rtol=1e-5, atol=1e-5)
    np.testing.assert_array_equal(np.asarray(mixed.mC), np.asarray(expected.mC))


def test_incremental_add_sample_residualizes_novel_positions():
    """Novel sites must use residualized mean, not a raw mC/uC rebuild."""
    s1 = create_temp_sample([10, 20], [8, 2], [2, 8], [0, 0])
    s2 = create_temp_sample([10, 20, 30], [2, 8, 9], [8, 2, 1], [0, 0, 0])

    def bump(_path, _pos, mean):
        return np.clip(np.asarray(mean, dtype=np.float64) + 0.2, 1e-6, 1.0 - 1e-6)

    streamed = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    streamed.residualize_apply = bump
    streamed.add_sample(s1)
    streamed.add_sample(s2)
    expected = streamed.finalize()

    first_only = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    first_only.residualize_apply = bump
    first_only.add_sample(s1)
    centroid = first_only.finalize()
    from methyl_utils.core.io import load_from_h5

    sample2 = load_from_h5(s2)
    mixed = centroid.add_sample(sample2, residualize_apply=bump, sample_path=str(s2))
    np.testing.assert_array_equal(np.asarray(mixed.pos), np.asarray(expected.pos))
    np.testing.assert_allclose(np.asarray(mixed.Sx), np.asarray(expected.Sx), rtol=1e-5, atol=1e-5)
    np.testing.assert_allclose(np.asarray(mixed.Sx2), np.asarray(expected.Sx2), rtol=1e-5, atol=1e-5)
    raw_new = 9.0 / 10.0
    adj_new = np.clip(raw_new + 0.2, 1e-6, 1.0 - 1e-6)
    pos30 = int(np.where(np.asarray(mixed.pos) == 30)[0][0])
    np.testing.assert_allclose(float(np.asarray(mixed.Sx)[pos30]), adj_new, rtol=1e-5, atol=1e-5)


def test_multiple_samples_are_averaged_correctly():
    # Two identical samples → averages should be the same
    pos = [1000, 2000]
    sample1 = create_temp_sample(pos, [10, 20], [5, 0], [0, 1])
    sample2 = create_temp_sample(pos, [10, 20], [5, 0], [0, 1])

    builder = MethylCentroidBuilder(min_coverage=1)
    builder.add_sample(sample1)
    builder.add_sample(sample2)
    centroid = builder.finalize()

    assert centroid.metadata.get("n_samples") == 2
    assert list(centroid.N) == [2, 2]
    np.testing.assert_array_equal(np.asarray(centroid.mC), [10, 20])  # exact avg
    np.testing.assert_array_equal(np.asarray(centroid.uC), [5, 0])


def test_coverage_filter_removes_low_coverage():
    sample1 = create_temp_sample([1, 2, 3], [10, 0, 1], [0, 0, 10], [0, 0, 0])
    sample2 = create_temp_sample([1, 2, 3], [10, 0, 1], [0, 0, 10], [0, 0, 0])

    builder = MethylCentroidBuilder(min_coverage=15)  # total cov per sample = 10, 0, 11 → after 2 samples: 20, 0, 22
    builder.add_sample(sample1)
    builder.add_sample(sample2)
    centroid = builder.finalize()

    # Position 2 has total coverage 0 → filtered out
    assert len(centroid) == 2
    assert set(centroid.pos.values) == {1, 3}


# --------------------------------------------------------------------------- #
# Property-based testing with Hypothesis (real confidence)
# --------------------------------------------------------------------------- #
if HAS_HYPOTHESIS:

    @settings(deadline=1000)
    @given(
        n_samples=st.integers(1, 20),
        n_positions=st.integers(1, 500),
        seed=st.integers(0, 2**32 - 1),
    )
    def test_accumulator_correctness_property(n_samples, n_positions, seed):
        rng = np.random.default_rng(seed)

        # Generate realistic random data
        positions = rng.integers(1, 10**9, size=n_positions, dtype=np.uint32)
        positions = np.unique(positions)
        positions.sort()
        n_pos = len(positions)

        # Create n_samples with some overlap
        sample_paths = []
        for i in range(n_samples):
            # Each sample covers 30–100% of positions
            subset = rng.choice([False, True], size=n_pos, p=[0.3, 0.7])
            if not subset.any():
                subset[0] = True

            pos_idx = np.where(subset)[0]
            mC = rng.integers(0, 50, size=len(pos_idx), dtype=np.uint32)
            uC = rng.integers(0, 50, size=len(pos_idx), dtype=np.uint32)
            tnc = rng.integers(0, 255, size=len(pos_idx), dtype=np.uint8)

            path = create_temp_sample(positions[pos_idx], mC.tolist(), uC.tolist(), tnc.tolist())
            sample_paths.append(path)

        builder = MethylCentroidBuilder(min_coverage=1)
        for p in sample_paths:
            builder.add_sample(p)

        centroid = builder.finalize()

        # Ground truth: manual accumulation
        truth_mC = np.zeros(n_pos, dtype=np.uint64)
        truth_uC = np.zeros(n_pos, dtype=np.uint64)
        truth_N = np.zeros(n_pos, dtype=np.uint32)
        truth_Sx = np.zeros(n_pos, dtype=np.float64)
        truth_Sx2 = np.zeros(n_pos, dtype=np.float64)
        truth_log_x = np.zeros(n_pos, dtype=np.float64)
        truth_log_1x = np.zeros(n_pos, dtype=np.float64)

        for path in sample_paths:
            # Re-load to accumulate manually
            sample = load_from_h5(path)
            idx = np.searchsorted(positions, sample.pos.values if hasattr(sample.pos, 'values') else np.asarray(sample.pos))
            mC = sample.mC.values if hasattr(sample.mC, 'values') else np.asarray(sample.mC)
            uC = sample.uC.values if hasattr(sample.uC, 'values') else np.asarray(sample.uC)
            cov = mC + uC
            mean = np.divide(mC, cov, where=cov > 0, out=np.zeros_like(mC, float))

            truth_mC[idx] += mC
            truth_uC[idx] += uC
            truth_N[idx] += 1
            truth_Sx[idx] += mean
            truth_Sx2[idx] += mean ** 2

            safe = np.clip(mean, 1e-10, 1 - 1e-10)
            truth_log_x[idx] += np.log(safe)
            truth_log_1x[idx] += np.log(1 - safe)

        # Compare
        mask = (truth_N > 0) & ((truth_mC + truth_uC) >= 1)
        final_pos = positions[mask]

        # Map centroid rows to global positions
        centroid_idx = np.searchsorted(final_pos, centroid.pos.values)
        assert np.all(centroid.pos.values == final_pos[centroid_idx])

        np.testing.assert_array_equal(centroid.N.values, truth_N[mask])
        np.testing.assert_array_almost_equal(
            centroid.mC.values, (truth_mC[mask] / truth_N[mask]).astype(np.uint32)
        )
        np.testing.assert_array_almost_equal(
            centroid.uC.values, (truth_uC[mask] / truth_N[mask]).astype(np.uint32)
        )
        np.testing.assert_array_almost_equal(centroid.Sx.values, truth_Sx[mask].astype(np.float32), decimal=5)
        np.testing.assert_array_almost_equal(centroid.Sx2.values, truth_Sx2[mask].astype(np.float32), decimal=5)
        # binned_stats (bin_edges, bin_counts) present when binned_stats_bins set
        assert centroid.binned_stats is not None
        assert "bin_edges" in centroid.binned_stats and "bin_counts" in centroid.binned_stats


def test_binned_stats_h5_schema_save_and_load():
    """Save centroid with binned_stats: H5 has only methylation_data.bins + bin_counts, no binned_stats group. Round-trip load restores binned_stats."""
    import h5py

    positions = [100, 200, 300]
    sample_path = create_temp_sample(positions, [5, 10, 15], [2, 0, 5], [0, 1, 2])
    builder = MethylCentroidBuilder(min_coverage=1, binned_stats_bins=20)
    builder.add_sample(sample_path)
    centroid = builder.finalize()
    assert centroid.binned_stats is not None

    with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        centroid.save_to_h5(path, compressed=False)
        with h5py.File(path, "r") as f:
            assert "binned_stats" not in f
            md = f["methylation_data"]
            assert "bins" in md.attrs
            assert md.attrs["bins"] == 20
            assert "bin_counts" in md
            bc = md["bin_counts"][:]
            assert bc.shape == (len(positions), 20)
        loaded = load_from_h5(path)
        assert loaded.binned_stats is not None
        assert "bin_edges" in loaded.binned_stats and "bin_counts" in loaded.binned_stats
        assert len(loaded.binned_stats["bin_edges"]) == 21
        np.testing.assert_array_almost_equal(loaded.binned_stats["bin_counts"], centroid.binned_stats["bin_counts"])
    finally:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# GPU path smoke test (only runs if CuPy available)
# --------------------------------------------------------------------------- #
def _centroid_arrays(centroid):
    return {
        "pos": np.asarray(centroid.pos),
        "N": np.asarray(centroid.N),
        "Sx": np.asarray(centroid.Sx),
        "Sx2": np.asarray(centroid.Sx2),
        "Sm": np.asarray(centroid.Sm),
        "Su": np.asarray(centroid.Su),
        "bins": np.asarray(centroid.binned_stats["bin_counts"]),
    }


def test_numpy_vs_explicit_numpy_backend():
    paths = [
        create_temp_sample([100, 200], [8, 2], [2, 8], [0, 0]),
        create_temp_sample([100, 300], [4, 6], [6, 4], [0, 1]),
    ]
    a = build_centroid(paths, min_coverage=1, use_gpu=False)
    b = build_centroid(paths, min_coverage=1, use_gpu=True, gpu_backend="numpy")
    for key, left in _centroid_arrays(a).items():
        np.testing.assert_allclose(left, _centroid_arrays(b)[key], rtol=1e-6, atol=1e-6)


def test_mojo_backend_matches_numpy():
    from methyl_utils.mojo_numeric import mojo_numeric_available

    if not mojo_numeric_available():
        pytest.skip("mojo-align numeric/ not on this host")
    paths = [
        create_temp_sample([100, 200], [8, 2], [2, 8], [0, 0]),
        create_temp_sample([100, 300], [4, 6], [6, 4], [0, 1]),
    ]
    cpu = build_centroid(paths, min_coverage=1, use_gpu=False)
    mojo = build_centroid(paths, min_coverage=1, use_gpu=True, gpu_backend="mojo")
    assert mojo._metadata.get("gpu_backend") == "mojo"
    for key, left in _centroid_arrays(cpu).items():
        np.testing.assert_allclose(left, _centroid_arrays(mojo)[key], rtol=1e-6, atol=1e-6)


@pytest.mark.skipif(not HAS_GPU, reason="CuPy not available")
def test_cupy_backend_matches_numpy():
    paths = [
        create_temp_sample([100, 200], [8, 2], [2, 8], [0, 0]),
        create_temp_sample([100, 300], [4, 6], [6, 4], [0, 1]),
    ]
    cpu = build_centroid(paths, min_coverage=1, use_gpu=False)
    gpu = build_centroid(paths, min_coverage=1, use_gpu=True, gpu_backend="cupy")
    for key, left in _centroid_arrays(cpu).items():
        np.testing.assert_allclose(left, _centroid_arrays(gpu)[key], rtol=1e-5, atol=1e-5)


@pytest.mark.skipif(not HAS_GPU, reason="CuPy not available")
def test_gpu_path_works():
    positions = [10, 20, 30]
    sample = create_temp_sample(positions, [5, 10, 15], [2, 0, 5], [0, 1, 2])

    builder = MethylCentroidBuilder(use_gpu=True)
    builder.add_sample(sample)
    centroid = builder.finalize()

    assert centroid.is_gpu is False  # finalize always returns CPU pandas
    assert len(centroid) == 3
    assert centroid.N.sum() == 3


# --------------------------------------------------------------------------- #
# Convenience function test
# --------------------------------------------------------------------------- #
def test_build_centroid_function():
    paths = [
        create_temp_sample([100], [8], [2], [0]),
        create_temp_sample([100], [12], [3], [0]),
    ]

    centroid = build_centroid(paths, min_coverage=1, use_gpu=False)

    assert isinstance(centroid, MethylCentroid)
    assert centroid._metadata.get("n_samples") == 2
    assert centroid.N.iloc[0] == 2
    # mC, uC are derived (Sm/N, Su/N): Sm=8+12=20, Su=2+3=5 → mC=10, uC=2
    assert centroid.mC.iloc[0] == 10
    assert centroid.uC.iloc[0] == 2


def test_centroid_schema_has_sm_su_sc2_swx2_and_properties():
    """Centroid has stored Sm, Su, Sc2, Swx2 and properties coverage, weighted_mean, weighted_variance."""
    positions = [100, 200]
    path = create_temp_sample(positions, [8, 12], [2, 3], [0, 1])
    centroid = build_centroid([path], min_coverage=1, use_gpu=False)
    assert hasattr(centroid, "Sm") and hasattr(centroid, "Su")
    assert hasattr(centroid, "Sc2") and hasattr(centroid, "Swx2")
    assert hasattr(centroid, "coverage") and hasattr(centroid, "weighted_mean") and hasattr(centroid, "weighted_variance")
    np.testing.assert_array_equal(np.asarray(centroid.coverage), [8 + 2, 12 + 3])
    np.testing.assert_array_almost_equal(
        np.asarray(centroid.weighted_mean), [8 / (8 + 2), 12 / (12 + 3)]
    )
    assert list(centroid.to_numpy().dtype.names) == [
        "pos", "tnc", "N", "Sx", "Sx2", "Sm", "Su", "Sc2", "Swx2"
    ]


if __name__ == "__main__":
    pytest.main(["-v", __file__])