"""Non-parametric genome-wide derived measures from per-chromosome H5 files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from methyl_utils import MethylSample

from ..config import DerivedMeasuresStepConfig


def _binary_entropy_mean(values: np.ndarray, weights: np.ndarray, *, eps: float = 1e-10) -> float:
    if values.size == 0:
        return float("nan")
    w = np.asarray(weights, dtype=np.float64)
    w_sum = float(np.sum(w))
    if w_sum <= 0.0:
        return float("nan")
    p = np.clip(np.asarray(values, dtype=np.float64), eps, 1.0 - eps)
    h = -(p * np.log2(p) + (1.0 - p) * np.log2(1.0 - p))
    return float(np.sum(w * h) / w_sum)


def _weighted_fraction(values: np.ndarray, weights: np.ndarray, lo: float, hi: float) -> float:
    w = np.asarray(weights, dtype=np.float64)
    w_sum = float(np.sum(w))
    if w_sum <= 0.0 or values.size == 0:
        return float("nan")
    mask = np.isfinite(values) & (values >= lo) & (values <= hi)
    return float(np.sum(w[mask]) / w_sum)


def _adjacent_disagreement_fraction(
    positions: np.ndarray,
    values: np.ndarray,
    coverage: np.ndarray,
    *,
    min_coverage: int,
    threshold: float,
) -> float:
    if positions.size < 2:
        return float("nan")
    order = np.argsort(positions, kind="mergesort")
    pos = positions[order]
    vals = values[order]
    cov = coverage[order]
    valid = cov >= int(min_coverage)
    if int(np.sum(valid)) < 2:
        return float("nan")
    diffs: List[float] = []
    for i in range(1, len(pos)):
        if not (valid[i] and valid[i - 1]):
            continue
        if int(pos[i] - pos[i - 1]) > 2:
            continue
        diffs.append(abs(float(vals[i]) - float(vals[i - 1])))
    if not diffs:
        return float("nan")
    return float(np.mean(np.asarray(diffs, dtype=np.float64) >= float(threshold)))


def _pmd_load_fraction(
    positions: np.ndarray,
    values: np.ndarray,
    coverage: np.ndarray,
    *,
    min_coverage: int,
    window_bp: int,
    step_bp: int,
    beta_threshold: float,
) -> float:
    if positions.size == 0:
        return float("nan")
    order = np.argsort(positions, kind="mergesort")
    pos = positions[order]
    vals = values[order]
    cov = coverage[order]
    valid = (cov >= int(min_coverage)) & np.isfinite(vals)
    if int(np.sum(valid)) == 0:
        return float("nan")
    pos = pos[valid]
    vals = vals[valid]
    start = int(pos.min())
    end = int(pos.max())
    if end <= start:
        return float("nan")
    hypo_windows = 0
    total_windows = 0
    w = int(max(1000, window_bp))
    s = int(max(500, step_bp))
    for left in range(start, max(start, end - w), s):
        right = left + w
        mask = (pos >= left) & (pos < right)
        if not np.any(mask):
            continue
        total_windows += 1
        if float(np.nanmean(vals[mask])) < float(beta_threshold):
            hypo_windows += 1
    if total_windows == 0:
        return float("nan")
    return float(hypo_windows / total_windows)


def _load_chrom_sample(
    sample_dir: str,
    chrom: str,
    context: str,
) -> Optional[MethylSample]:
    path = Path(sample_dir) / f"{chrom}-{context}.h5"
    if not path.is_file():
        return None
    try:
        return MethylSample.load_from_h5(path)
    except Exception:
        return None


def compute_sample_genome_measures(
    sample_id: str,
    sample_dir: str,
    *,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    cfg: DerivedMeasuresStepConfig,
    cohort_median_coverages: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    hypo_t = float(cfg.hypo_beta_threshold) if cfg.hypo_beta_threshold is not None else 0.2
    im_lo = float(cfg.intermediate_beta_lo) if cfg.intermediate_beta_lo is not None else 0.25
    im_hi = float(cfg.intermediate_beta_hi) if cfg.intermediate_beta_hi is not None else 0.75
    pmd_t = float(cfg.pmd_beta_threshold) if cfg.pmd_beta_threshold is not None else 0.3
    pmd_w = int(cfg.pmd_window_bp) if cfg.pmd_window_bp is not None else 100_000
    pmd_s = int(cfg.pmd_step_bp) if cfg.pmd_step_bp is not None else 50_000
    pdr_t = float(cfg.pdr_disagreement_threshold) if cfg.pdr_disagreement_threshold is not None else 0.25
    min_cov = int(cfg.min_coverage) if cfg.min_coverage is not None else 1

    all_beta: List[np.ndarray] = []
    all_cov: List[np.ndarray] = []
    all_pos: List[np.ndarray] = []
    chrom_cov_medians: Dict[str, float] = {}
    chrom_mean_beta: Dict[str, float] = {}
    chrom_pdr_proxy: Dict[str, float] = {}
    chrom_pmd_load: Dict[str, float] = {}
    x_beta_vals: List[float] = []
    auto_beta_vals: List[float] = []

    for chrom in chromosomes:
        chrom_betas: List[np.ndarray] = []
        chrom_covs: List[np.ndarray] = []
        chrom_positions: List[np.ndarray] = []
        for ctx in contexts:
            sample = _load_chrom_sample(sample_dir, str(chrom), str(ctx))
            if sample is None:
                continue
            beta = np.asarray(sample.get_methylation_levels(), dtype=np.float64)
            cov = np.asarray(sample.get_coverage(), dtype=np.float64)
            pos = np.asarray(sample.pos, dtype=np.uint32)
            n = min(beta.size, cov.size, pos.size)
            if n <= 0:
                continue
            beta = beta[:n]
            cov = cov[:n]
            pos = pos[:n]
            mask = cov >= min_cov
            if not np.any(mask):
                continue
            chrom_betas.append(beta[mask])
            chrom_covs.append(cov[mask])
            chrom_positions.append(pos[mask])
            all_beta.append(beta[mask])
            all_cov.append(cov[mask])
            all_pos.append(pos[mask])
            if str(chrom).upper() in {"X", "23"}:
                x_beta_vals.extend(beta[mask].tolist())
            else:
                auto_beta_vals.extend(beta[mask].tolist())

        if chrom_betas:
            c_beta = np.concatenate(chrom_betas)
            c_cov = np.concatenate(chrom_covs)
            chrom_mean_beta[str(chrom)] = float(np.average(c_beta, weights=c_cov))
            chrom_cov_medians[str(chrom)] = float(np.median(c_cov))
        if chrom_positions:
            pos_cat = np.concatenate(chrom_positions)
            beta_cat = np.concatenate(chrom_betas)
            cov_cat = np.concatenate(chrom_covs)
            chrom_pdr_proxy[str(chrom)] = _adjacent_disagreement_fraction(
                pos_cat, beta_cat, cov_cat, min_coverage=min_cov, threshold=pdr_t
            )
            chrom_pmd_load[str(chrom)] = _pmd_load_fraction(
                pos_cat,
                beta_cat,
                cov_cat,
                min_coverage=min_cov,
                window_bp=pmd_w,
                step_bp=pmd_s,
                beta_threshold=pmd_t,
            )
        else:
            chrom_pdr_proxy[str(chrom)] = float("nan")
            chrom_pmd_load[str(chrom)] = float("nan")

    row: Dict[str, Any] = {str(cfg.sample_id_column): str(sample_id)}

    if all_beta:
        beta_cat = np.concatenate(all_beta)
        cov_cat = np.concatenate(all_cov)
        row["genome::global_mean_beta"] = float(np.average(beta_cat, weights=cov_cat))
        row["genome::global_entropy"] = _binary_entropy_mean(beta_cat, cov_cat)
        row["genome::global_hypo_frac"] = _weighted_fraction(beta_cat, cov_cat, 0.0, hypo_t)
        row["genome::intermediate_meth_frac"] = _weighted_fraction(beta_cat, cov_cat, im_lo, im_hi)
        row["genome::median_coverage"] = float(np.median(cov_cat))
        row["genome::mean_coverage"] = float(np.mean(cov_cat))
        row["genome::covered_cpg_fraction"] = float(np.mean(cov_cat >= min_cov))
    else:
        for key in (
            "genome::global_mean_beta",
            "genome::global_entropy",
            "genome::global_hypo_frac",
            "genome::intermediate_meth_frac",
            "genome::median_coverage",
            "genome::mean_coverage",
            "genome::covered_cpg_fraction",
        ):
            row[key] = float("nan")

    if x_beta_vals and auto_beta_vals:
        row["genome::x_vs_autosome_mean_beta_delta"] = float(np.mean(x_beta_vals) - np.mean(auto_beta_vals))
    else:
        row["genome::x_vs_autosome_mean_beta_delta"] = float("nan")

    for chrom in chromosomes:
        c_key_mean = f"genome::chrom_{chrom}::mean_beta"
        row[c_key_mean] = float(chrom_mean_beta.get(str(chrom), float("nan")))
        c_med = chrom_cov_medians.get(str(chrom), float("nan"))
        row[f"genome::chrom_{chrom}::median_coverage"] = c_med
        ref = (cohort_median_coverages or {}).get(str(chrom))
        if ref is not None and np.isfinite(c_med) and ref > 0:
            row[f"genome::chrom_{chrom}::coverage_zscore"] = float((c_med - ref) / ref)
        else:
            row[f"genome::chrom_{chrom}::coverage_zscore"] = float("nan")

        row[f"genome::chrom_{chrom}::pdr_proxy"] = float(
            chrom_pdr_proxy.get(str(chrom), float("nan"))
        )
        row[f"genome::chrom_{chrom}::pmd_load"] = float(
            chrom_pmd_load.get(str(chrom), float("nan"))
        )

    return row


def compute_cohort_median_coverages(
    samples: Sequence[Tuple[str, str]],
    *,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    min_coverage: int,
) -> Dict[str, float]:
    acc: Dict[str, List[float]] = {str(c): [] for c in chromosomes}
    for _sid, sample_dir in samples:
        for chrom in chromosomes:
            covs: List[float] = []
            for ctx in contexts:
                sample = _load_chrom_sample(sample_dir, str(chrom), str(ctx))
                if sample is None:
                    continue
                cov = np.asarray(sample.get_coverage(), dtype=np.float64)
                if cov.size:
                    covs.append(float(np.median(cov[cov >= min_coverage])))
            if covs:
                acc[str(chrom)].append(float(np.median(covs)))
    return {c: float(np.median(v)) for c, v in acc.items() if v}
