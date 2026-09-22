"""
Panel-scoped chromosome / sample-level derived features (non-parametric).

Uses empirical methylation levels and centroid binned_stats histograms only —
no Beta-distribution approximations.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.stats import entropy as scipy_entropy
from scipy.stats import wasserstein_distance

from methyl_utils.methyl_centroid_pair import MethylCentroidPair


def _feature_label_token(label: object) -> str:
    token = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in token)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_")
    return cleaned or "cancer"


def _weighted_jensen_shannon_distance(
    values_a: np.ndarray,
    values_b: np.ndarray,
    weights: np.ndarray,
    eps: float = 1e-10,
) -> float:
    if values_a.size == 0 or values_b.size == 0 or values_a.size != values_b.size:
        return float("nan")
    if weights.size != values_a.size:
        return float("nan")
    w = np.abs(np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0))
    w_sum = float(np.sum(w))
    if w_sum <= 0.0:
        return float("nan")
    w = w / w_sum
    p = np.clip(np.asarray(values_a, dtype=np.float64), eps, 1.0 - eps)
    q = np.clip(np.asarray(values_b, dtype=np.float64), eps, 1.0 - eps)
    p_w = p * w
    q_w = q * w
    p_sum = float(np.sum(p_w))
    q_sum = float(np.sum(q_w))
    if p_sum <= 0.0 or q_sum <= 0.0:
        return float("nan")
    p_w = p_w / p_sum
    q_w = q_w / q_sum
    m = 0.5 * (p_w + q_w)
    js_div = 0.5 * (scipy_entropy(p_w, m, base=2) + scipy_entropy(q_w, m, base=2))
    if not np.isfinite(js_div):
        return float("nan")
    return float(np.sqrt(max(float(js_div), 0.0)))


def _binary_entropy(values: np.ndarray, weights: np.ndarray, *, eps: float = 1e-10) -> float:
    if values.size == 0:
        return float("nan")
    w = np.abs(np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0))
    w_sum = float(np.sum(w))
    if w_sum <= 0.0:
        return float("nan")
    p = np.clip(np.asarray(values, dtype=np.float64), eps, 1.0 - eps)
    h = -(p * np.log2(p) + (1.0 - p) * np.log2(1.0 - p))
    return float(np.sum(w * h) / w_sum)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    if values.size == 0:
        return float("nan")
    w = np.abs(np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0))
    w_sum = float(np.sum(w))
    if w_sum <= 0.0:
        return float("nan")
    return float(np.sum(w * values) / w_sum)


def _weighted_fraction_in_range(
    values: np.ndarray,
    weights: np.ndarray,
    lo: float,
    hi: float,
) -> float:
    if values.size == 0:
        return float("nan")
    w = np.abs(np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0))
    mask = np.isfinite(values) & (values >= lo) & (values <= hi)
    w_sum = float(np.sum(w))
    if w_sum <= 0.0:
        return float("nan")
    return float(np.sum(w[mask]) / w_sum)


def _discrete_hellinger(p: np.ndarray, q: np.ndarray, *, eps: float = 1e-12) -> float:
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    if p.size == 0 or q.size == 0 or p.shape != q.shape:
        return float("nan")
    p = np.clip(p, eps, None)
    q = np.clip(q, eps, None)
    p = p / float(np.sum(p))
    q = q / float(np.sum(q))
    bc = float(np.sum(np.sqrt(p * q)))
    return float(np.sqrt(max(1.0 - bc, 0.0)))


def _histogram_wasserstein(
    bin_edges: np.ndarray,
    counts_a: np.ndarray,
    counts_b: np.ndarray,
) -> float:
    edges = np.asarray(bin_edges, dtype=np.float64)
    ca = np.asarray(counts_a, dtype=np.float64)
    cb = np.asarray(counts_b, dtype=np.float64)
    if edges.size < 2 or ca.size != cb.size or ca.size != edges.size - 1:
        return float("nan")
    if float(np.sum(ca)) <= 0.0 or float(np.sum(cb)) <= 0.0:
        return float("nan")
    centers = 0.5 * (edges[:-1] + edges[1:])
    wa = ca / float(np.sum(ca))
    wb = cb / float(np.sum(cb))
    try:
        return float(wasserstein_distance(centers, centers, wa, wb))
    except Exception:
        return float("nan")


def _sample_histogram_from_values(
    values: np.ndarray,
    weights: np.ndarray,
    bin_edges: np.ndarray,
) -> np.ndarray:
    edges = np.asarray(bin_edges, dtype=np.float64)
    if values.size == 0 or edges.size < 2:
        return np.zeros((max(len(edges) - 1, 0),), dtype=np.float64)
    w = np.abs(np.nan_to_num(weights, nan=0.0, posinf=0.0, neginf=0.0))
    vals = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
    n_bins = len(edges) - 1
    counts = np.zeros((n_bins,), dtype=np.float64)
    bin_idx = np.searchsorted(edges, vals, side="right") - 1
    bin_idx = np.clip(bin_idx, 0, max(0, n_bins - 1))
    for b in range(n_bins):
        mask = bin_idx == b
        if np.any(mask):
            counts[b] = float(np.sum(w[mask]))
    return counts


def _centroid_mean_histogram(counts: np.ndarray, bin_edges: np.ndarray) -> np.ndarray:
    counts = np.asarray(counts, dtype=np.float64)
    if counts.ndim != 2 or counts.shape[0] == 0:
        return np.zeros((max(len(bin_edges) - 1, 0),), dtype=np.float64)
    row_totals = np.sum(counts, axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        probs = np.where(row_totals > 0.0, counts / row_totals, 0.0)
    return np.mean(probs, axis=0)


def chromosome_feature_names(
    chromosomes: Sequence[str],
    *,
    class_labels: Sequence[str],
    distance_metrics: Sequence[str],
    include_sample_globals: bool = True,
) -> List[str]:
    names: List[str] = []
    metrics = [str(m).strip().lower() for m in distance_metrics if str(m).strip()]
    for chrom in chromosomes:
        c = str(chrom)
        names.extend(
            [
                f"chrom::{c}::mean_beta",
                f"chrom::{c}::entropy",
                f"chrom::{c}::obs_fraction",
            ]
        )
        for label in class_labels:
            suffix = _feature_label_token(label)
            for metric in metrics:
                names.append(f"chrom::{c}::{metric}_to__{suffix}")
        if len(class_labels) >= 2:
            names.append(f"chrom::{c}::js_margin")
    if include_sample_globals:
        names.extend(
            [
                "sample::global_mean_beta",
                "sample::global_entropy",
                "sample::global_hypo_frac",
                "sample::global_intermediate_meth_frac",
                "sample::global_obs_fraction",
                "sample::js_margin",
            ]
        )
        for label in class_labels:
            suffix = _feature_label_token(label)
            for metric in metrics:
                names.append(f"sample::{metric}_to__{suffix}")
    return names


def _resolve_chromosomes(
    locus_df: pd.DataFrame,
    configured: Optional[Sequence[str]],
) -> List[str]:
    if configured:
        return [str(c) for c in configured]
    if locus_df.empty or "chromosome" not in locus_df.columns:
        return []
    return sorted({str(c) for c in locus_df["chromosome"].astype(str).tolist()})


def _prepare_centroid_histograms_by_chrom(
    locus_df: pd.DataFrame,
    centroid_dir_by_class_label: Dict[str, str],
    class_labels: Sequence[str],
    healthy_label: str,
) -> Tuple[Optional[np.ndarray], Dict[str, Dict[str, np.ndarray]]]:
    """Return (bin_edges, {class_label: {chromosome: mean_histogram}})."""
    if locus_df.empty or not centroid_dir_by_class_label:
        return None, {}
    healthy_dir = centroid_dir_by_class_label.get(str(healthy_label))
    if not healthy_dir:
        return None, {}

    bin_edges_ref: Optional[np.ndarray] = None
    out: Dict[str, Dict[str, np.ndarray]] = {}
    by_chrom_indices: Dict[str, np.ndarray] = {}
    for chrom, cdf in locus_df.groupby("chromosome", sort=False):
        by_chrom_indices[str(chrom)] = cdf.index.to_numpy(dtype=np.int64)

    for class_label in class_labels:
        class_dir = centroid_dir_by_class_label.get(str(class_label))
        if not class_dir:
            continue
        per_chrom: Dict[str, np.ndarray] = {}
        for chrom, idxs in by_chrom_indices.items():
            sub_df = locus_df.loc[idxs, ["position", "context"]].reset_index(drop=True)
            if class_label == healthy_label:
                loaded = MethylCentroidPair.load_binned_counts_from_centroids(
                    sub_df,
                    centroid1_dir=healthy_dir,
                    centroid2_dir=healthy_dir,
                    chromosome=str(chrom),
                )
            else:
                loaded = MethylCentroidPair.load_binned_counts_from_centroids(
                    sub_df,
                    centroid1_dir=healthy_dir,
                    centroid2_dir=class_dir,
                    chromosome=str(chrom),
                )
            if loaded is None:
                continue
            edges, healthy_sub, cancer_sub = loaded
            edges = np.asarray(edges, dtype=np.float64)
            if class_label == healthy_label:
                counts = healthy_sub
            else:
                counts = cancer_sub
            counts = np.asarray(counts, dtype=np.float64)
            if counts.ndim != 2 or counts.shape[0] != len(sub_df):
                continue
            if bin_edges_ref is None:
                bin_edges_ref = edges
            elif bin_edges_ref.shape != edges.shape or not np.allclose(bin_edges_ref, edges):
                continue
            per_chrom[str(chrom)] = _centroid_mean_histogram(counts, edges)
        if per_chrom:
            out[str(class_label)] = per_chrom
    return bin_edges_ref, out


def compute_chromosome_feature_matrix(
    X_raw: np.ndarray,
    locus_df: pd.DataFrame,
    weights: np.ndarray,
    *,
    class_labels: Sequence[str],
    healthy_class_label: Optional[str],
    centroid_dir_by_class_label: Optional[Dict[str, str]] = None,
    hypo_beta_threshold: Optional[float] = None,
    intermediate_beta_lo: Optional[float] = None,
    intermediate_beta_hi: Optional[float] = None,
    distance_metrics: Optional[Sequence[str]] = None,
    chromosomes: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, List[str], Dict[str, Any]]:
    """
    Build samples x chromosome-feature matrix from panel loci (effect_size-weighted).
    """
    n_samples = int(X_raw.shape[0])
    n_loci = int(X_raw.shape[1])
    if n_loci <= 0 or locus_df.empty:
        return np.zeros((n_samples, 0), dtype=np.float32), [], {"n_features": 0}

    hypo_t = float(hypo_beta_threshold) if hypo_beta_threshold is not None else 0.2
    im_lo = float(intermediate_beta_lo) if intermediate_beta_lo is not None else 0.25
    im_hi = float(intermediate_beta_hi) if intermediate_beta_hi is not None else 0.75
    metrics = [str(m).strip().lower() for m in (distance_metrics or ("js", "hellinger", "wasserstein"))]
    metrics = [m for m in metrics if m in {"js", "hellinger", "wasserstein"}] or ["js", "hellinger", "wasserstein"]

    w = np.abs(np.asarray(weights, dtype=np.float64).reshape(-1))
    if w.size != n_loci:
        w = np.ones((n_loci,), dtype=np.float64)
    chrom_list = _resolve_chromosomes(locus_df, chromosomes)
    labels = [str(x) for x in class_labels if str(x).strip()]
    healthy = str(healthy_class_label or (labels[0] if labels else "")).strip()
    if healthy and healthy not in labels:
        labels = [healthy] + labels

    feature_names = chromosome_feature_names(
        chrom_list,
        class_labels=labels,
        distance_metrics=metrics,
        include_sample_globals=True,
    )
    X_out = np.full((n_samples, len(feature_names)), np.nan, dtype=np.float32)
    idx = {name: j for j, name in enumerate(feature_names)}

    chrom_to_indices: Dict[str, np.ndarray] = {}
    if "chromosome" in locus_df.columns:
        for chrom in chrom_list:
            mask = locus_df["chromosome"].astype(str).values == str(chrom)
            chrom_to_indices[str(chrom)] = np.where(mask)[0].astype(np.int64)

    bin_edges, class_hist_by_chrom = _prepare_centroid_histograms_by_chrom(
        locus_df,
        centroid_dir_by_class_label or {},
        labels,
        healthy,
    )

    def _distance(
        metric: str,
        sample_vals: np.ndarray,
        sample_w: np.ndarray,
        ref_hist: np.ndarray,
    ) -> float:
        if bin_edges is None or ref_hist.size == 0:
            return float("nan")
        sample_hist = _sample_histogram_from_values(sample_vals, sample_w, bin_edges)
        if float(np.sum(sample_hist)) <= 0.0:
            return float("nan")
        if metric == "js":
            ref_vals = np.repeat(
                0.5 * (bin_edges[:-1] + bin_edges[1:]),
                np.maximum(np.round(ref_hist * 1000).astype(int), 0),
            )
            samp_vals = np.repeat(
                0.5 * (bin_edges[:-1] + bin_edges[1:]),
                np.maximum(np.round(sample_hist / max(float(np.sum(sample_hist)), 1e-12) * 1000).astype(int), 0),
            )
            if ref_vals.size == 0 or samp_vals.size == 0:
                return float("nan")
            ref_mean = np.mean(ref_vals) if ref_vals.size else 0.5
            samp_mean = np.mean(samp_vals) if samp_vals.size else 0.5
            return _weighted_jensen_shannon_distance(
                np.array([samp_mean], dtype=np.float64),
                np.array([ref_mean], dtype=np.float64),
                np.array([1.0], dtype=np.float64),
            )
        if metric == "hellinger":
            return _discrete_hellinger(sample_hist, ref_hist)
        if metric == "wasserstein":
            return _histogram_wasserstein(bin_edges, sample_hist, ref_hist)
        return float("nan")

    for i in range(n_samples):
        row = np.asarray(X_raw[i, :], dtype=np.float64)
        obs_mask = np.isfinite(row)
        global_vals = row[obs_mask]
        global_w = w[obs_mask]
        if global_vals.size > 0:
            X_out[i, idx["sample::global_mean_beta"]] = _weighted_mean(global_vals, global_w)
            X_out[i, idx["sample::global_entropy"]] = _binary_entropy(global_vals, global_w)
            X_out[i, idx["sample::global_hypo_frac"]] = _weighted_fraction_in_range(
                global_vals, global_w, 0.0, hypo_t
            )
            X_out[i, idx["sample::global_intermediate_meth_frac"]] = _weighted_fraction_in_range(
                global_vals, global_w, im_lo, im_hi
            )
            X_out[i, idx["sample::global_obs_fraction"]] = float(np.sum(global_w) / max(float(np.sum(w)), 1e-12))

        per_label_dist: Dict[str, Dict[str, float]] = {m: {} for m in metrics}
        for chrom in chrom_list:
            loci_idx = chrom_to_indices.get(str(chrom), np.asarray([], dtype=np.int64))
            if loci_idx.size == 0:
                continue
            c_mask = obs_mask[loci_idx]
            if not np.any(c_mask):
                continue
            c_vals = row[loci_idx][c_mask]
            c_w = w[loci_idx][c_mask]
            c_key = str(chrom)
            X_out[i, idx[f"chrom::{c_key}::mean_beta"]] = _weighted_mean(c_vals, c_w)
            X_out[i, idx[f"chrom::{c_key}::entropy"]] = _binary_entropy(c_vals, c_w)
            X_out[i, idx[f"chrom::{c_key}::obs_fraction"]] = float(
                np.sum(c_w) / max(float(np.sum(w[loci_idx])), 1e-12)
            )
            chrom_dists: Dict[str, Dict[str, float]] = {m: {} for m in metrics}
            for label in labels:
                ref_hist = (class_hist_by_chrom.get(label) or {}).get(c_key)
                if ref_hist is None:
                    continue
                suffix = _feature_label_token(label)
                for metric in metrics:
                    dist = _distance(metric, c_vals, c_w, ref_hist)
                    feat = f"chrom::{c_key}::{metric}_to__{suffix}"
                    if feat in idx:
                        X_out[i, idx[feat]] = dist
                    chrom_dists[metric][label] = dist
            if len(labels) >= 2 and "js" in metrics:
                h_label = healthy if healthy in labels else labels[0]
                other_labels = [l for l in labels if l != h_label]
                if other_labels:
                    o_label = other_labels[0]
                    d_h = chrom_dists.get("js", {}).get(h_label, float("nan"))
                    d_o = chrom_dists.get("js", {}).get(o_label, float("nan"))
                    if np.isfinite(d_h) and np.isfinite(d_o):
                        margin_key = f"chrom::{c_key}::js_margin"
                        if margin_key in idx:
                            X_out[i, idx[margin_key]] = float(d_h - d_o)

        if global_vals.size > 0 and bin_edges is not None:
            global_hist = _sample_histogram_from_values(global_vals, global_w, bin_edges)
            sample_dists: Dict[str, Dict[str, float]] = {m: {} for m in metrics}
            for label in labels:
                # Pool centroid histograms across chromosomes (effect_size-weighted).
                pooled = np.zeros((len(bin_edges) - 1,), dtype=np.float64)
                pool_w = 0.0
                for chrom in chrom_list:
                    ref_hist = (class_hist_by_chrom.get(label) or {}).get(str(chrom))
                    if ref_hist is None:
                        continue
                    loci_idx = chrom_to_indices.get(str(chrom), np.asarray([], dtype=np.int64))
                    c_w_sum = float(np.sum(w[loci_idx])) if loci_idx.size else 0.0
                    if c_w_sum <= 0.0:
                        continue
                    pooled += ref_hist * c_w_sum
                    pool_w += c_w_sum
                if pool_w <= 0.0:
                    continue
                ref_pooled = pooled / pool_w
                suffix = _feature_label_token(label)
                for metric in metrics:
                    dist = _distance(metric, global_vals, global_w, ref_pooled)
                    feat = f"sample::{metric}_to__{suffix}"
                    if feat in idx:
                        X_out[i, idx[feat]] = dist
                    sample_dists[metric][label] = dist
            if len(labels) >= 2 and "js" in metrics:
                h_label = healthy if healthy in labels else labels[0]
                other_labels = [l for l in labels if l != h_label]
                if other_labels:
                    d_h = sample_dists.get("js", {}).get(h_label, float("nan"))
                    d_o = sample_dists.get("js", {}).get(other_labels[0], float("nan"))
                    if np.isfinite(d_h) and np.isfinite(d_o) and "sample::js_margin" in idx:
                        X_out[i, idx["sample::js_margin"]] = float(d_h - d_o)

    report: Dict[str, Any] = {
        "n_features": int(len(feature_names)),
        "chromosomes": list(chrom_list),
        "distance_metrics": list(metrics),
        "hypo_beta_threshold": hypo_t,
        "intermediate_beta_lo": im_lo,
        "intermediate_beta_hi": im_hi,
    }
    return X_out, feature_names, report


def _derived_measures_fingerprint(feature_names: Sequence[str]) -> str:
    import hashlib

    payload = "|".join(str(x) for x in feature_names)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_ecdf_derived_measures_schema(
    dmps_df: pd.DataFrame,
    *,
    effect_size_weights: np.ndarray,
    class_labels: Sequence[str],
    healthy_class_label: str,
    centroid_dir_by_class_label: Optional[Dict[str, str]] = None,
    chromosome_hypo_beta_threshold: Optional[float] = None,
    chromosome_intermediate_beta_lo: Optional[float] = None,
    chromosome_intermediate_beta_hi: Optional[float] = None,
    chromosome_distance_metrics: Optional[Sequence[str]] = None,
    chromosome_list: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Build persisted schema for blind-predict derived-measure assembly (ECDF pickle extension)."""
    if dmps_df is None or dmps_df.empty:
        return None
    locus_df = dmps_df.copy()
    if "chromosome" not in locus_df.columns:
        locus_df["chromosome"] = "unknown"
    if "position" not in locus_df.columns and "pos" in locus_df.columns:
        locus_df["position"] = locus_df["pos"]
    if "context" not in locus_df.columns:
        locus_df["context"] = "CG"
    chrom_list = _resolve_chromosomes(locus_df, chromosome_list)
    metrics = [str(m).strip().lower() for m in (chromosome_distance_metrics or ("js", "hellinger", "wasserstein"))]
    metrics = [m for m in metrics if m in {"js", "hellinger", "wasserstein"}] or ["js", "hellinger", "wasserstein"]
    labels = [str(x) for x in class_labels if str(x).strip()]
    healthy = str(healthy_class_label or (labels[0] if labels else "")).strip()
    feature_names = chromosome_feature_names(
        chrom_list,
        class_labels=labels,
        distance_metrics=metrics,
        include_sample_globals=True,
    )
    if not feature_names:
        return None
    w = np.abs(np.asarray(effect_size_weights, dtype=np.float64).reshape(-1))
    if w.size != len(locus_df):
        w = np.ones((len(locus_df),), dtype=np.float64)
    loci_records: List[Dict[str, Any]] = []
    for row_idx, row in locus_df.reset_index(drop=True).iterrows():
        loci_records.append(
            {
                "chromosome": str(row.get("chromosome", "unknown")),
                "context": str(row.get("context", "CG")),
                "position": int(row.get("position", row.get("pos", 0))),
                "weight": float(w[int(row_idx)]),
            }
        )
    return {
        "feature_names": list(feature_names),
        "feature_order_fingerprint": _derived_measures_fingerprint(feature_names),
        "effect_size_weights": [float(x) for x in w.tolist()],
        "loci": loci_records,
        "class_labels": labels,
        "healthy_class_label": healthy,
        "centroid_dir_by_class_label": {
            str(k): str(v) for k, v in (centroid_dir_by_class_label or {}).items() if str(v).strip()
        },
        "chromosome_hypo_beta_threshold": chromosome_hypo_beta_threshold,
        "chromosome_intermediate_beta_lo": chromosome_intermediate_beta_lo,
        "chromosome_intermediate_beta_hi": chromosome_intermediate_beta_hi,
        "chromosome_distance_metrics": list(metrics),
        "chromosome_list": list(chrom_list),
    }


def compute_ecdf_derived_features_from_schema(
    X_raw_row: np.ndarray,
    schema: Dict[str, Any],
) -> Tuple[np.ndarray, List[str]]:
    """Compute one sample row of derived features using a frozen ECDF schema."""
    feature_names = list(schema.get("feature_names") or [])
    if not feature_names:
        return np.zeros((0,), dtype=np.float64), []
    expected_fp = str(schema.get("feature_order_fingerprint") or "")
    if expected_fp and expected_fp != _derived_measures_fingerprint(feature_names):
        raise ValueError(
            "Derived-measures feature fingerprint mismatch; model schema does not match builder."
        )
    loci = schema.get("loci") or []
    locus_df = pd.DataFrame(loci)
    if locus_df.empty:
        return np.full((len(feature_names),), np.nan, dtype=np.float64), feature_names
    weights = np.asarray(schema.get("effect_size_weights") or [], dtype=np.float64)
    if weights.size != len(locus_df):
        weights = np.ones((len(locus_df),), dtype=np.float64)
    row = np.asarray(X_raw_row, dtype=np.float64).reshape(1, -1)
    if row.shape[1] != len(locus_df):
        raise ValueError(
            f"Derived-measures locus count mismatch: schema has {len(locus_df)} loci, "
            f"sample vector has {row.shape[1]} values."
        )
    X_feat, out_names, _report = compute_chromosome_feature_matrix(
        row,
        locus_df,
        weights,
        class_labels=list(schema.get("class_labels") or []),
        healthy_class_label=str(schema.get("healthy_class_label") or ""),
        centroid_dir_by_class_label=dict(schema.get("centroid_dir_by_class_label") or {}),
        hypo_beta_threshold=schema.get("chromosome_hypo_beta_threshold"),
        intermediate_beta_lo=schema.get("chromosome_intermediate_beta_lo"),
        intermediate_beta_hi=schema.get("chromosome_intermediate_beta_hi"),
        distance_metrics=schema.get("chromosome_distance_metrics"),
        chromosomes=schema.get("chromosome_list"),
    )
    if out_names != feature_names:
        raise ValueError("Derived-measures feature name order mismatch between schema and builder.")
    return np.asarray(X_feat[0], dtype=np.float64), feature_names
