from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import h5py
import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages/methylutils"))
sys.path.insert(0, str(REPO_ROOT / "packages/methyldetector"))

from methyl_detector.core.methyldetector import MethylDetector
from methyl_detector.models.config import MethylDetectorConfig
from methyl_utils.core.centroid_builder import MethylCentroidBuilder
from methyl_utils.methyl_centroid_pair import DIST_ECDF, MethylCentroidPair


def create_sample(path: Path, positions, mC, uC, tnc) -> Path:
    with h5py.File(path, "w") as f:
        group = f.create_group("methylation_data")
        group.create_dataset("pos", data=np.asarray(positions, dtype=np.uint32))
        group.create_dataset("mC", data=np.asarray(mC, dtype=np.uint32))
        group.create_dataset("uC", data=np.asarray(uC, dtype=np.uint32))
        group.create_dataset("tnc", data=np.asarray(tnc, dtype=np.uint8))
    return path


def build_detector(temp_dir: str, **overrides) -> MethylDetector:
    centroid1_dir = Path(temp_dir) / "c1"
    centroid2_dir = Path(temp_dir) / "c2"
    centroid1_dir.mkdir(exist_ok=True)
    centroid2_dir.mkdir(exist_ok=True)
    config = MethylDetectorConfig.model_validate(
        {
            "chromosome": "1",
            "contexts": ["CG"],
            "centroid1_dir": str(centroid1_dir),
            "centroid2_dir": str(centroid2_dir),
            "output_dir": str(Path(temp_dir) / "out"),
            **overrides,
        }
    )
    return MethylDetector(config)


def test_methyl_detector_config_rejects_legacy_distribution_fields():
    with TemporaryDirectory() as temp_dir:
        centroid1_dir = Path(temp_dir) / "c1"
        centroid2_dir = Path(temp_dir) / "c2"
        centroid1_dir.mkdir()
        centroid2_dir.mkdir()

        with pytest.raises(
            ValueError,
            match="Legacy distribution-specific detector options are no longer supported",
        ):
            MethylDetectorConfig.model_validate(
                {
                    "chromosome": "1",
                    "contexts": ["CG"],
                    "centroid1_dir": str(centroid1_dir),
                    "centroid2_dir": str(centroid2_dir),
                    "distribution": "beta",
                }
            )


def test_methyl_detector_config_rejects_removed_statistical_test_option():
    with TemporaryDirectory() as temp_dir:
        centroid1_dir = Path(temp_dir) / "c1"
        centroid2_dir = Path(temp_dir) / "c2"
        centroid1_dir.mkdir()
        centroid2_dir.mkdir()

        with pytest.raises(
            ValueError,
            match="statistical_test is no longer supported",
        ):
            MethylDetectorConfig.model_validate(
                {
                    "chromosome": "1",
                    "contexts": ["CG"],
                    "centroid1_dir": str(centroid1_dir),
                    "centroid2_dir": str(centroid2_dir),
                    "statistical_test": "mann_whitney",
                }
            )


def test_methyl_detector_config_persists_validation_split_fields():
    """validation_split_ratio / validation_n_repeats must survive model_validate (not stripped)."""
    with TemporaryDirectory() as temp_dir:
        centroid1_dir = Path(temp_dir) / "c1"
        centroid2_dir = Path(temp_dir) / "c2"
        centroid1_dir.mkdir()
        centroid2_dir.mkdir()
        cfg = MethylDetectorConfig.model_validate(
            {
                "chromosome": "1",
                "contexts": ["CG"],
                "centroid1_dir": str(centroid1_dir),
                "centroid2_dir": str(centroid2_dir),
                "output_dir": str(Path(temp_dir) / "out"),
                "validation_split_ratio": 0.25,
                "validation_n_repeats": 2,
            }
        )
        assert cfg.validation_split_ratio == pytest.approx(0.25)
        assert cfg.validation_n_repeats == 2


def test_methyl_detector_config_rejects_legacy_ecdf_grid_alias():
    with TemporaryDirectory() as temp_dir:
        centroid1_dir = Path(temp_dir) / "c1"
        centroid2_dir = Path(temp_dir) / "c2"
        centroid1_dir.mkdir()
        centroid2_dir.mkdir()

        with pytest.raises(ValueError, match="ecdf_overlap_grid_size and ecdf_ks_grid_size"):
            MethylDetectorConfig.model_validate(
                {
                    "chromosome": "1",
                    "contexts": ["CG"],
                    "centroid1_dir": str(centroid1_dir),
                    "centroid2_dir": str(centroid2_dir),
                    "ecdf_overlap_grid_size": 512,
                }
            )


def test_methyl_centroid_pair_is_ecdf_only():
    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        sample1 = create_sample(
            temp_root / "sample1.h5",
            [1000, 2000],
            [10, 16],
            [10, 4],
            [1, 1],
        )
        sample2 = create_sample(
            temp_root / "sample2.h5",
            [1000, 2000],
            [2, 4],
            [18, 16],
            [1, 1],
        )

        builder1 = MethylCentroidBuilder(min_coverage=1, use_gpu=False, binned_stats_bins=20)
        builder1.add_sample(sample1)
        centroid1 = builder1.finalize()

        builder2 = MethylCentroidBuilder(min_coverage=1, use_gpu=False, binned_stats_bins=20)
        builder2.add_sample(sample2)
        centroid2 = builder2.finalize()

        pair = MethylCentroidPair(min_coverage=1)
        result = pair.compare_centroids(centroid1, centroid2)

        assert not result.empty
        assert set(result["dist"].unique()) == {DIST_ECDF}


def test_methyl_centroid_pair_uses_histogram_mann_whitney():
    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        sample1 = create_sample(
            temp_root / "sample1.h5",
            [1000, 2000],
            [10, 16],
            [10, 4],
            [1, 1],
        )
        sample2 = create_sample(
            temp_root / "sample2.h5",
            [1000, 2000],
            [2, 4],
            [18, 16],
            [1, 1],
        )

        builder1 = MethylCentroidBuilder(min_coverage=1, use_gpu=False, binned_stats_bins=20)
        builder1.add_sample(sample1)
        centroid1 = builder1.finalize()

        builder2 = MethylCentroidBuilder(min_coverage=1, use_gpu=False, binned_stats_bins=20)
        builder2.add_sample(sample2)
        centroid2 = builder2.finalize()

        pair = MethylCentroidPair(min_coverage=1)
        result = pair.compare_centroids(centroid1, centroid2)

        assert not result.empty
        assert "tau2_1" in result.columns
        assert "tau2_2" in result.columns
        assert np.isfinite(result["p_value"]).all()


def test_effect_size_coverage_is_applied_per_context():
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(temp_dir, effect_size_coverage=0.75)
        dmps_df = pd.DataFrame(
            {
                "position": [1000, 1001, 2000, 2001, 2002],
                "context": ["CG", "CG", "CHH", "CHH", "CHH"],
                "effect_size": [0.9, 0.1, 0.45, 0.35, 0.2],
                "statistical_dmp": [True, True, True, True, False],
            }
        )

        filtered = detector._filter_biological_dmps(dmps_df)

        assert list(filtered["position"]) == [1000, 2000, 2001]


def test_prepare_validation_splits_uses_all_data_when_ratio_is_zero():
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(
            temp_dir,
            validation_split_ratio=0.0,
            validation_n_repeats=3,
        )
        y = np.array([0, 0, 0, 1, 1, 1], dtype=int)

        splits = detector._prepare_validation_splits(y, require_holdout=True)

        # When validation_split_ratio=0, all samples are used (no holdout); one split (all, all)
        assert len(splits) == 1
        calib_idx, test_idx = splits[0]
        assert len(calib_idx) == len(y)
        assert len(test_idx) == len(y)
        assert set(calib_idx) == set(test_idx) == set(range(len(y)))


def test_tau2_filter_drops_only_high_heterogeneity_positions():
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(temp_dir, max_tau2_for_dmp=0.2)
        comparison_results = pd.DataFrame(
            {
                "position": [1000, 1001, 1002],
                "tau2_1": [0.3, 0.1, 0.4],
                "tau2_2": [0.4, 0.5, 0.1],
            }
        )

        filtered = detector._apply_tau2_filter(comparison_results, context="CG")

        assert list(filtered["position"]) == [1001, 1002]


def test_prefix_cache_matches_direct_validation(monkeypatch):
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(
            temp_dir,
            validation_split_ratio=0.5,
            validation_n_repeats=1,
        )
        sorted_df = pd.DataFrame(
            {
                "position": [1000, 2000],
                "context": ["CG", "CG"],
                "chromosome": ["1", "1"],
                "effect_size": [1.0, 0.5],
                "delta_sign": [1, -1],
                "mean1": [0.9, 0.1],
                "mean2": [0.1, 0.9],
            }
        )
        bin_edges = np.array([0.0, 0.5, 1.0], dtype=np.float64)
        bc1 = np.array([[0.0, 10.0], [10.0, 0.0]], dtype=np.float64)
        bc2 = np.array([[10.0, 0.0], [0.0, 10.0]], dtype=np.float64)
        # No real centroid H5 in temp_dir; skip intersection (production uses real centroids).
        monkeypatch.setattr(detector, "_subset_dmps_to_both_centroids", lambda df, log_drops=True: df)
        monkeypatch.setattr(
            detector,
            "_extract_bin_counts_for_dmps",
            lambda df: (bin_edges, bc1[: len(df)], bc2[: len(df)]),
        )

        X_val = np.array(
            [
                [0.90, 0.10],
                [0.85, 0.15],
                [0.10, 0.90],
                [0.15, 0.85],
            ],
            dtype=np.float64,
        )
        y_val = np.array([0, 0, 1, 1], dtype=int)
        splits = detector._prepare_validation_splits(y_val, require_holdout=True)

        cache = detector._build_validation_prefix_cache(sorted_df, X_val, y_val, splits)
        cached_result = detector._evaluate_prefix_subset(cache, k=2)

        calib_idx, test_idx = splits[0]
        direct_result = detector._validate_classifier_subset(
            sorted_df,
            X_val[calib_idx],
            y_val[calib_idx],
            X_val[test_idx],
            y_val[test_idx],
            sorted_df["position"].values,
            sorted_df["context"].values,
        )

        assert cached_result["balanced_accuracy"] == pytest.approx(
            direct_result["balanced_accuracy"],
            abs=1e-9,
        )


def test_merge_validation_results_merges_training_metrics():
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(temp_dir)
        base_holdout = {
            "balanced_accuracy": 0.9,
            "confusion_matrix": {"tp": 1, "tn": 1, "fp": 0, "fn": 0},
            "metrics": {
                "sensitivity": 1.0,
                "specificity": 1.0,
                "accuracy": 1.0,
                "precision": 1.0,
            },
            "counts": {"n_positive": 1, "n_negative": 1, "n_total": 2},
            "split_balanced_accuracy_std": 0.0,
            "n_splits": 1,
        }
        base_train = {
            "balanced_accuracy": 0.85,
            "confusion_matrix": {"tp": 1, "tn": 0, "fp": 1, "fn": 0},
            "metrics": {
                "sensitivity": 1.0,
                "specificity": 0.0,
                "accuracy": 0.5,
                "precision": 0.5,
            },
            "counts": {"n_positive": 1, "n_negative": 1, "n_total": 2},
            "split_balanced_accuracy_std": 0.0,
            "n_splits": 1,
        }
        r1 = {**base_holdout, "training_metrics": dict(base_train)}
        r2 = {
            **base_holdout,
            "balanced_accuracy": 0.7,
            "confusion_matrix": {"tp": 0, "tn": 2, "fp": 0, "fn": 0},
            "metrics": {
                "sensitivity": 0.0,
                "specificity": 1.0,
                "accuracy": 1.0,
                "precision": 0.0,
            },
            "counts": {"n_positive": 0, "n_negative": 2, "n_total": 2},
            "training_metrics": {
                **base_train,
                "balanced_accuracy": 0.95,
                "confusion_matrix": {"tp": 2, "tn": 0, "fp": 0, "fn": 0},
                "metrics": {
                    "sensitivity": 1.0,
                    "specificity": 0.0,
                    "accuracy": 1.0,
                    "precision": 1.0,
                },
                "counts": {"n_positive": 2, "n_negative": 0, "n_total": 2},
            },
        }
        merged = detector._merge_validation_results([r1, r2])
        assert merged["training_metrics"]["n_splits"] == 2
        assert merged["training_metrics"]["balanced_accuracy"] == pytest.approx(0.9, abs=1e-9)
        assert merged["training_metrics"]["confusion_matrix"]["tp"] == 3


def test_featurecuts_logs_dual_self_check_panels(monkeypatch):
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(
            temp_dir,
            classifier_dmp_selection="featurecuts_validation",
            target_balanced_accuracy=0.95,
            min_core_dmps=10,
        )
        sorted_df = pd.DataFrame(
            {
                "position": np.arange(1000, 1020, dtype=np.uint32),
                "context": ["CG"] * 20,
                "effect_size": np.linspace(1.0, 0.1, 20),
                "mean1": np.linspace(0.2, 0.3, 20),
                "mean2": np.linspace(0.7, 0.6, 20),
            }
        )

        monkeypatch.setattr(
            detector,
            "_featurecuts_select_k",
            lambda pool: (pool.iloc[:4].copy().reset_index(drop=True), {"balanced_accuracy": 0.9783}),
        )

        check_calls = []

        def _fake_self_check(dmps_df, *, check_name=None):
            check_calls.append((len(dmps_df), check_name))

        monkeypatch.setattr(detector, "_check_centroid_self_classification", _fake_self_check)

        out = detector._classifier_dmps_from_sorted(sorted_df)
        assert len(out) == 10
        assert check_calls == [
            (4, "featurecuts-target-k=4"),
            (10, "featurecuts-final-k=10"),
        ]


def test_featurecuts_strict_ba_rejection_raises(monkeypatch):
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(
            temp_dir,
            classifier_dmp_selection="featurecuts_validation",
            target_balanced_accuracy=0.95,
            fail_if_below_target=True,
        )
        sorted_df = pd.DataFrame(
            {
                "position": np.arange(1000, 1005, dtype=np.uint32),
                "context": ["CG"] * 5,
                "effect_size": np.linspace(1.0, 0.5, 5),
            }
        )

        monkeypatch.setattr(
            detector,
            "_featurecuts_select_k",
            lambda pool: (_ for _ in ()).throw(
                ValueError("FeatureCuts balanced accuracy 0.8000 below target 0.9500")
            ),
        )
        elbow_called = {"n": 0}

        def _elbow_should_not_run(*args, **kwargs):
            elbow_called["n"] += 1
            return sorted_df

        monkeypatch.setattr(detector, "_effect_size_elbow_trim", _elbow_should_not_run)

        with pytest.raises(ValueError, match="below target 0.9500"):
            detector._classifier_dmps_from_sorted(sorted_df)
        assert elbow_called["n"] == 0


def test_featurecuts_select_k_strict_ba_raises_not_swallowed(monkeypatch):
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(
            temp_dir,
            classifier_dmp_selection="featurecuts_validation",
            target_balanced_accuracy=0.95,
            fail_if_below_target=True,
        )
        pool = pd.DataFrame(
            {
                "position": np.arange(1000, 1005, dtype=np.uint32),
                "context": ["CG"] * 5,
                "effect_size": np.linspace(1.0, 0.5, 5),
            }
        )

        monkeypatch.setattr(
            detector,
            "_load_validation_samples_multicontext",
            lambda df: (np.zeros((2, 5)), np.array([0, 1]), None, None),
        )
        monkeypatch.setattr(detector, "_prepare_validation_splits", lambda y, require_holdout=True: [(0, 1)])
        monkeypatch.setattr(
            detector,
            "_build_validation_prefix_cache",
            lambda *args, **kwargs: {},
        )
        monkeypatch.setattr(detector, "_optimize_dmps_binary_search", lambda *args, **kwargs: 3)
        monkeypatch.setattr(
            detector,
            "_evaluate_prefix_subset",
            lambda cache, k: {"balanced_accuracy": 0.80},
        )
        monkeypatch.setattr(
            detector,
            "_full_validation_merge_for_selected_panel",
            lambda *args, **kwargs: None,
        )

        with pytest.raises(ValueError, match="below target 0.9500"):
            detector._featurecuts_select_k(pool)


def test_featurecuts_failure_does_not_fall_back_to_elbow(monkeypatch):
    with TemporaryDirectory() as temp_dir:
        detector = build_detector(
            temp_dir,
            classifier_dmp_selection="featurecuts_validation",
        )
        sorted_df = pd.DataFrame(
            {
                "position": np.arange(1000, 1010, dtype=np.uint32),
                "context": ["CG"] * 10,
                "effect_size": np.linspace(1.0, 0.1, 10),
            }
        )

        monkeypatch.setattr(detector, "_featurecuts_select_k", lambda pool: (None, None))
        elbow_called = {"n": 0}

        def _elbow_should_not_run(*args, **kwargs):
            elbow_called["n"] += 1
            return sorted_df.iloc[:3].copy().reset_index(drop=True)

        monkeypatch.setattr(detector, "_effect_size_elbow_trim", _elbow_should_not_run)

        with pytest.raises(ValueError, match="FeatureCuts did not produce a classifier panel"):
            detector._classifier_dmps_from_sorted(sorted_df)
        assert elbow_called["n"] == 0
