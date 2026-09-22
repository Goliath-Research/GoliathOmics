import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages/methylutils"))
sys.path.insert(0, str(REPO_ROOT / "packages/methyldetector"))

import methyl_detector.explorer as explorer_module
from methyl_detector.explorer import MethylDetectorExplorer


def _min_n_filter_indices_local(n1, n2, min_N, min_N_pct):
    n1 = np.asarray(n1, dtype=np.float64).ravel()
    n2 = np.asarray(n2, dtype=np.float64).ravel()
    if min_N is not None:
        keep = (n1 >= min_N) & (n2 >= min_N)
    else:
        max_n = np.maximum(n1, n2)
        min_n = np.minimum(n1, n2)
        keep = (max_n > 0) & (min_n >= min_N_pct * max_n)
    return np.where(keep)[0].astype(np.intp)


class MockCentroid:
    def __init__(self, means, variances, counts, n=30, positions=None):
        self.N = np.full(len(means), float(n), dtype=np.float64)
        self.mean = np.asarray(means, dtype=np.float64)
        self.variance = np.asarray(variances, dtype=np.float64)
        self.Sx = self.mean * self.N
        self.Sx2 = self.variance * np.maximum(self.N - 1.0, 1.0) + (self.Sx ** 2) / self.N
        self.pos = np.asarray(positions if positions is not None else range(len(means)), dtype=np.uint32)
        self.coverage = np.full(len(means), 100, dtype=np.int64)  # Sm+Su for min_coverage filter
        self.binned_stats = {
            "bin_edges": np.linspace(0.0, 1.0, np.asarray(counts).shape[1] + 1, dtype=np.float64),
            "bin_counts": np.asarray(counts, dtype=np.float64),
        }


class TestMinNFilter:
    def test_absolute_min_N(self):
        idx = _min_n_filter_indices_local(
            np.array([3, 10, 5, 10, 10]),
            np.array([10, 2, 10, 10, 10]),
            min_N=5,
            min_N_pct=0.05,
        )
        np.testing.assert_array_equal(idx, [2, 3, 4])

    def test_min_N_pct(self):
        idx = _min_n_filter_indices_local(
            np.array([1, 1, 0, 2]),
            np.array([20, 10, 5, 20]),
            min_N=None,
            min_N_pct=0.05,
        )
        np.testing.assert_array_equal(idx, [0, 1, 3])


def test_explorer_lambda_var_optimization_reports_consistently(monkeypatch):
    positions = np.array([101, 102, 103, 104], dtype=np.uint32)
    centroid1 = MockCentroid(
        means=[0.10, 0.15, 0.50, 0.85],
        variances=[0.004, 0.006, 0.020, 0.005],
        counts=[
            [12, 1, 0, 0],
            [10, 2, 0, 0],
            [4, 5, 4, 5],
            [0, 0, 1, 12],
        ],
        positions=positions,
    )
    centroid2 = MockCentroid(
        means=[0.12, 0.82, 0.52, 0.20],
        variances=[0.004, 0.008, 0.020, 0.006],
        counts=[
            [11, 2, 0, 0],
            [0, 0, 2, 10],
            [5, 4, 5, 4],
            [10, 2, 0, 0],
        ],
        positions=positions,
    )

    monkeypatch.setattr(
        explorer_module.MethylCentroidPair,
        "load_and_align",
        staticmethod(lambda *args, **kwargs: (centroid1, centroid2, positions)),
    )

    explorer = MethylDetectorExplorer(
        centroid1_path="dummy1.h5",
        centroid2_path="dummy2.h5",
        alpha=0.2,
        min_coverage=1,
        min_samples_abs=1,
        min_samples_pct=0.05,
        delta_mean_reduction=0.2,
        lambda_var=0.5,
        optimize_lambda_var=True,
        lambda_var_min=0.0,
        lambda_var_max=2.0,
        lambda_var_step=0.5,
        ecdf_overlap_grid_size=256,
    )
    df, report = explorer.run()

    assert report["total_positions"] == 4
    assert report["positions_after_delta_mean_reduction"] >= report["positions_after_statistical_filter"] >= len(df)
    assert "lambda_var_used" in report
    assert "effect_size_vs_1_minus_q_correlation" in report
    assert "effect_size_ecdf" in df.columns
    if len(df) > 0:
        assert df["effect_size"].notna().all()
        assert df["overlap"].notna().all()


def test_explorer_runs_mann_whitney_on_delta_reduced_positions(monkeypatch):
    positions = np.array([101, 102, 103, 104], dtype=np.uint32)
    centroid1 = MockCentroid(
        means=[0.10, 0.15, 0.50, 0.85],
        variances=[0.004, 0.006, 0.020, 0.005],
        counts=[
            [12, 1, 0, 0],
            [10, 2, 0, 0],
            [4, 5, 4, 5],
            [0, 0, 1, 12],
        ],
        positions=positions,
    )
    centroid2 = MockCentroid(
        means=[0.12, 0.82, 0.52, 0.20],
        variances=[0.004, 0.008, 0.020, 0.006],
        counts=[
            [11, 2, 0, 0],
            [0, 0, 2, 10],
            [5, 4, 5, 4],
            [10, 2, 0, 0],
        ],
        positions=positions,
    )

    monkeypatch.setattr(
        explorer_module.MethylCentroidPair,
        "load_and_align",
        staticmethod(lambda *args, **kwargs: (centroid1, centroid2, positions)),
    )

    call_info = {}

    def fake_mann_whitney(bc1, bc2, n1, n2):
        call_info["rows"] = int(np.asarray(bc1).shape[0])
        return {
            "u_stat": np.zeros(call_info["rows"], dtype=np.float64),
            "z_stat": np.zeros(call_info["rows"], dtype=np.float64),
            "p_value": np.full(call_info["rows"], 0.01, dtype=np.float64),
            "var_u": np.ones(call_info["rows"], dtype=np.float64),
        }

    monkeypatch.setattr(
        explorer_module,
        "mann_whitney_from_bin_counts",
        fake_mann_whitney,
    )

    explorer = MethylDetectorExplorer(
        centroid1_path="dummy1.h5",
        centroid2_path="dummy2.h5",
        alpha=0.05,
        min_coverage=1,
        delta_mean_reduction=0.2,
        ecdf_overlap_grid_size=64,
    )
    df, report = explorer.run()

    assert call_info["rows"] == report["positions_after_delta_mean_reduction"] == 2
    assert report["positions_after_delta_mean_reduction"] >= report["positions_after_statistical_filter"] >= len(df)
