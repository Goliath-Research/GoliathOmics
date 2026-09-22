"""MethylDetectorExplorer: staged statistical and biological effect-size analysis."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd
from scipy.stats import rankdata

logger = logging.getLogger(__name__)

try:
    from methyl_utils import MethylCentroidPair, storey_qvalues
    from methyl_utils.statistical_tests import (
        mann_whitney_from_bin_counts,
        ecdf_overlap_integral,
        effect_size_from_components,
        optimize_lambda_var,
    )
    from methyl_utils.core.distribution_views import ECDFView
except ImportError as e:
    raise ImportError(
        "MethylDetectorExplorer requires methyl_utils (MethylUtils). "
        "Install it from the monorepo (e.g. packages/methylutils)."
    ) from e


def _require_binned_stats(centroid: Any) -> None:
    binned = getattr(centroid, "binned_stats", None)
    if not binned or "bin_edges" not in binned or "bin_counts" not in binned:
        raise ValueError(
            "Centroids must have binned_stats (bin_edges, bin_counts) for MethylDetectorExplorer. "
            "Build centroids with binned_stats_bins (e.g. 20)."
        )


def _to_arr(x: Any) -> np.ndarray:
    if hasattr(x, "values"):
        return np.asarray(x.values, dtype=np.float64).ravel()
    return np.asarray(x, dtype=np.float64).ravel()


def _effective_min_samples(min_samples_abs: int, min_samples_pct: float, cohort_size: int) -> int:
    """Effective minimum N per position for a centroid with given cohort size (same as detector config)."""
    from math import ceil
    return max(min_samples_abs, ceil(min_samples_pct * cohort_size))


def _min_samples_cohort_size_from_centroid(centroid: Any) -> int:
    """Cohort size for min_samples_pct; align with MethylDetector._min_samples_cohort_size (no project paths)."""
    meta = getattr(centroid, "metadata", None) or {}
    ns = meta.get("n_samples")
    if ns is not None:
        try:
            return max(1, int(ns))
        except (TypeError, ValueError):
            pass
    raw = meta.get("sample_paths") or meta.get("samples_used")
    if isinstance(raw, (list, tuple)) and len(raw) > 0:
        return max(1, len(raw))
    if getattr(centroid, "N", None) is not None:
        nmax = int(np.max(_to_arr(centroid.N)))
        return max(1, nmax)
    return 1


class MethylDetectorExplorer:
    """Analyze the staged detector pipeline with a single canonical effect_size."""

    def __init__(
        self,
        centroid1_path: Union[str, Path],
        centroid2_path: Union[str, Path],
        alpha: float = 0.05,
        min_coverage: int = 4,
        min_samples_abs: int = 1,
        min_samples_pct: float = 0.05,
        delta_mean_reduction: Optional[float] = None,
        min_delta_mean: Optional[float] = None,
        max_overlap: Optional[float] = None,
        min_effect_size: Optional[float] = None,
        lambda_var: float = 2.0,
        optimize_lambda_var: bool = False,
        lambda_var_min: float = 0.0,
        lambda_var_max: float = 6.0,
        lambda_var_step: float = 0.25,
        ecdf_overlap_grid_size: int = 512,
    ):
        self.centroid1_path = Path(centroid1_path)
        self.centroid2_path = Path(centroid2_path)
        self.alpha = float(alpha)
        self.min_coverage = int(min_coverage)
        self.min_samples_abs = int(min_samples_abs)
        self.min_samples_pct = float(min_samples_pct)
        self.delta_mean_reduction = delta_mean_reduction
        self.min_delta_mean = min_delta_mean
        self.max_overlap = max_overlap
        self.min_effect_size = min_effect_size
        self.lambda_var = float(lambda_var)
        self.optimize_lambda_var = bool(optimize_lambda_var)
        self.lambda_var_min = float(lambda_var_min)
        self.lambda_var_max = float(lambda_var_max)
        self.lambda_var_step = float(lambda_var_step)
        self.ecdf_overlap_grid_size = int(ecdf_overlap_grid_size)
        self._report: Optional[Dict[str, Any]] = None
        self._result_df: Optional[pd.DataFrame] = None

    def run(self) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        t0 = time.perf_counter()
        centroid1, centroid2, common_pos = MethylCentroidPair.load_and_align(
            str(self.centroid1_path),
            str(self.centroid2_path),
            min_coverage=self.min_coverage,
        )
        _require_binned_stats(centroid1)
        _require_binned_stats(centroid2)

        # Per-centroid valid sets, then intersection (same logic as MethylDetector)
        n1_cohort = _min_samples_cohort_size_from_centroid(centroid1)
        n2_cohort = _min_samples_cohort_size_from_centroid(centroid2)
        min_s1 = _effective_min_samples(self.min_samples_abs, self.min_samples_pct, n1_cohort)
        min_s2 = _effective_min_samples(self.min_samples_abs, self.min_samples_pct, n2_cohort)
        pair = MethylCentroidPair(min_coverage=self.min_coverage, min_samples=(min_s1, min_s2))
        candidate_pos = pair._align_centroids(centroid1, centroid2)
        keep_mask = np.isin(common_pos, candidate_pos)
        keep_idx = np.where(keep_mask)[0].astype(np.intp)
        if len(keep_idx) == 0:
            self._result_df = pd.DataFrame()
            self._report = {
                "total_positions": int(len(common_pos)),
                "positions_after_min_samples_filter": 0,
                "positions_after_statistical_filter": 0,
                "positions_after_delta_mean_reduction": 0,
                "positions_after_biological_filter": 0,
                "lambda_var_used": self.lambda_var,
                "time_total_s": time.perf_counter() - t0,
            }
            return self._result_df, self._report

        # N and data at common positions, then at kept positions
        pos1 = np.asarray(centroid1.pos)
        pos2 = np.asarray(centroid2.pos)
        idx1_common = np.searchsorted(pos1, common_pos, side="left")
        idx2_common = np.searchsorted(pos2, common_pos, side="left")
        n1_at_common = _to_arr(centroid1.N)[idx1_common]
        n2_at_common = _to_arr(centroid2.N)[idx2_common]
        mean1_at_common = _to_arr(centroid1.mean)[idx1_common]
        mean2_at_common = _to_arr(centroid2.mean)[idx2_common]
        var1_at_common = _to_arr(centroid1.variance)[idx1_common]
        var2_at_common = _to_arr(centroid2.variance)[idx2_common]

        delta_gate = self.delta_mean_reduction
        positions_min_n = common_pos[keep_idx]
        mean1 = mean1_at_common[keep_idx]
        mean2 = mean2_at_common[keep_idx]
        var1 = var1_at_common[keep_idx]
        var2 = var2_at_common[keep_idx]
        n1 = n1_at_common[keep_idx]
        n2 = n2_at_common[keep_idx]
        signed_delta = mean1 - mean2
        delta_mean = np.abs(signed_delta)
        bc1_at_common = np.asarray(centroid1.binned_stats["bin_counts"], dtype=np.float64)[idx1_common]
        bc2_at_common = np.asarray(centroid2.binned_stats["bin_counts"], dtype=np.float64)[idx2_common]
        bc1 = bc1_at_common[keep_idx]
        bc2 = bc2_at_common[keep_idx]

        prefiltered_mask = np.ones(len(positions_min_n), dtype=bool)
        if delta_gate is not None:
            prefiltered_mask = delta_mean >= float(delta_gate)

        prefiltered_positions = positions_min_n[prefiltered_mask]
        prefiltered_mean1 = mean1[prefiltered_mask]
        prefiltered_mean2 = mean2[prefiltered_mask]
        prefiltered_delta = delta_mean[prefiltered_mask]
        prefiltered_var1 = var1[prefiltered_mask]
        prefiltered_var2 = var2[prefiltered_mask]
        prefiltered_n1 = n1[prefiltered_mask]
        prefiltered_n2 = n2[prefiltered_mask]
        prefiltered_bc1 = bc1[prefiltered_mask]
        prefiltered_bc2 = bc2[prefiltered_mask]

        if len(prefiltered_positions) > 0:
            stat_result = mann_whitney_from_bin_counts(
                prefiltered_bc1,
                prefiltered_bc2,
                n1=prefiltered_n1,
                n2=prefiltered_n2,
            )
            p_values = np.asarray(stat_result["p_value"], dtype=np.float64)
            q_values, _ = storey_qvalues(p_values)
        else:
            p_values = np.asarray([], dtype=np.float64)
            q_values = np.asarray([], dtype=np.float64)

        prefiltered_df = pd.DataFrame(
            {
                "position": prefiltered_positions,
                "mean1": prefiltered_mean1,
                "mean2": prefiltered_mean2,
                "delta_mean": prefiltered_delta,
                "variance1": prefiltered_var1,
                "variance2": prefiltered_var2,
                "n1": prefiltered_n1,
                "n2": prefiltered_n2,
                "p_value": p_values,
                "q_value": np.asarray(q_values, dtype=np.float64),
            }
        )

        stat_df = prefiltered_df[prefiltered_df["q_value"] <= self.alpha].copy()

        if len(stat_df) > 0:
            pos_to_idx = {int(p): i for i, p in enumerate(common_pos)}
            idx_in_common = np.asarray([pos_to_idx[int(p)] for p in stat_df["position"].values], dtype=np.intp)
            bin_edges = np.asarray(centroid1.binned_stats["bin_edges"], dtype=np.float64)
            bc1 = np.asarray(centroid1.binned_stats["bin_counts"], dtype=np.float64)[idx_in_common]
            bc2 = np.asarray(centroid2.binned_stats["bin_counts"], dtype=np.float64)[idx_in_common]
            sx1 = _to_arr(centroid1.Sx)[idx_in_common]
            sx2 = _to_arr(centroid2.Sx)[idx_in_common]
            sx2_1 = _to_arr(centroid1.Sx2)[idx_in_common]
            sx2_2 = _to_arr(centroid2.Sx2)[idx_in_common]
            N1 = _to_arr(centroid1.N)[idx_in_common]
            N2 = _to_arr(centroid2.N)[idx_in_common]
            view1 = ECDFView(bin_edges, bc1, sx1, N1, sx2_1)
            view2 = ECDFView(bin_edges, bc2, sx2, N2, sx2_2)
            overlap = ecdf_overlap_integral(
                view1,
                view2,
                np.arange(len(stat_df), dtype=np.intp),
                grid_size=self.ecdf_overlap_grid_size,
            )
            lambda_used = self.lambda_var
            lambda_corr = None
            if self.optimize_lambda_var and len(stat_df) > 1:
                lambda_values = np.arange(
                    self.lambda_var_min,
                    self.lambda_var_max + self.lambda_var_step * 0.5,
                    self.lambda_var_step,
                    dtype=np.float64,
                )
                optimized = optimize_lambda_var(
                    delta_mean=stat_df["delta_mean"].values.astype(np.float64),
                    overlap=overlap,
                    var1=stat_df["variance1"].values.astype(np.float64),
                    var2=stat_df["variance2"].values.astype(np.float64),
                    target_scores=1.0 - stat_df["q_value"].values.astype(np.float64),
                    lambda_values=lambda_values,
                )
                lambda_used = float(optimized["lambda_var"])
                lambda_corr = optimized["correlation"]
                effect_size = np.asarray(optimized["effect_size"], dtype=np.float64)
                reliability = effect_size_from_components(
                    delta_mean=stat_df["delta_mean"].values.astype(np.float64),
                    overlap=overlap,
                    var1=stat_df["variance1"].values.astype(np.float64),
                    var2=stat_df["variance2"].values.astype(np.float64),
                    lambda_var=lambda_used,
                )["reliability"]
            else:
                effect = effect_size_from_components(
                    delta_mean=stat_df["delta_mean"].values.astype(np.float64),
                    overlap=overlap,
                    var1=stat_df["variance1"].values.astype(np.float64),
                    var2=stat_df["variance2"].values.astype(np.float64),
                    lambda_var=lambda_used,
                )
                effect_size = effect["effect_size"]
                reliability = effect["reliability"]

            stat_df["overlap"] = overlap.astype(np.float64)
            stat_df["effect_size"] = effect_size.astype(np.float64)
            stat_df["effect_size_reliability"] = np.asarray(reliability, dtype=np.float64)
            ranks = rankdata(stat_df["effect_size"].values.astype(np.float64))
            stat_df["effect_size_ecdf"] = (ranks - 0.5) / len(ranks)
        else:
            lambda_used = self.lambda_var
            lambda_corr = None
            stat_df["overlap"] = np.nan
            stat_df["effect_size"] = np.nan
            stat_df["effect_size_reliability"] = np.nan
            stat_df["effect_size_ecdf"] = np.nan

        bio_df = stat_df.copy()
        if self.min_delta_mean is not None and len(bio_df) > 0:
            bio_df = bio_df[bio_df["delta_mean"].astype(float) >= float(self.min_delta_mean)].copy()
        if self.max_overlap is not None and len(bio_df) > 0:
            bio_df = bio_df[bio_df["overlap"].astype(float) <= float(self.max_overlap)].copy()
        if self.min_effect_size is not None and len(bio_df) > 0:
            bio_df = bio_df[bio_df["effect_size"].astype(float) >= float(self.min_effect_size)].copy()
        if "effect_size" in bio_df.columns and len(bio_df) > 0:
            bio_df = bio_df.sort_values("effect_size", ascending=False).reset_index(drop=True)

        report = {
            "total_positions": int(len(common_pos)),
            "positions_after_min_samples_filter": int(len(positions_min_n)),
            "positions_after_delta_mean_reduction": int(len(prefiltered_df)),
            "positions_after_statistical_filter": int(len(stat_df)),
            "positions_after_biological_filter": int(len(bio_df)),
            "alpha": self.alpha,
            "delta_mean_reduction_used": delta_gate,
            "lambda_var_used": float(lambda_used),
            "ecdf_overlap_grid_size": self.ecdf_overlap_grid_size,
            "time_total_s": time.perf_counter() - t0,
        }
        if lambda_corr is not None:
            report["effect_size_vs_1_minus_q_correlation"] = float(lambda_corr)
        if len(stat_df) > 0 and np.isfinite(stat_df["effect_size"]).any():
            effect_vals = stat_df["effect_size"].astype(float).values
            report["effect_size_90th_percentile"] = float(np.percentile(effect_vals, 90))
            report["effect_size_95th_percentile"] = float(np.percentile(effect_vals, 95))
            report["effect_size_99th_percentile"] = float(np.percentile(effect_vals, 99))

        self._report = report
        self._result_df = bio_df
        return bio_df, report

    @property
    def report(self) -> Optional[Dict[str, Any]]:
        return self._report

    @property
    def result_df(self) -> Optional[pd.DataFrame]:
        return self._result_df

