"""Cohort feature matrix loader (samples x features), shared across omics packs.

Analyte-agnostic analogue of the methylation fraction extractor used by the tabular
sklearn backend. ``transform`` maps raw values (counts / intensities) to a modeling
scale, ``normalize`` removes per-sample scale, and ``impute`` fills missing features.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import h5py
import numpy as np

from .feature_store import find_feature_h5, read_feature_datasets


def _read_h5(path: Path) -> Tuple[List[str], np.ndarray]:
    with h5py.File(path, "r") as h5:
        feature_ids, values = read_feature_datasets(h5)
    return [str(f) for f in feature_ids], values


def _apply_transform(X: np.ndarray, transform: str, *, min_total: float) -> np.ndarray:
    if transform == "count" or transform == "none":
        return X
    if transform in ("cpm", "logcpm"):
        lib = X.sum(axis=1, keepdims=True)
        lib[lib < min_total] = min_total
        cpm = X / lib * 1e6
        return np.log2(cpm + 1.0) if transform == "logcpm" else cpm
    if transform == "log2":
        return np.log2(X + 1.0)
    raise ValueError(f"unknown transform: {transform!r}")


def _apply_normalize(X: np.ndarray, normalize: str) -> np.ndarray:
    """Per-sample normalization on the (already transformed) matrix."""
    if normalize in ("none", None):
        return X
    if normalize == "median":
        # Center each sample on the cohort's per-sample median (removes loading scale).
        row_med = np.nanmedian(X, axis=1, keepdims=True)
        grand = np.nanmedian(row_med)
        return X - row_med + grand
    if normalize == "quantile":
        # Rank-based quantile normalization to a common reference distribution.
        ref = np.nanmean(np.sort(np.where(np.isnan(X), np.nanmin(X), X), axis=1), axis=0)
        out = np.empty_like(X)
        for i in range(X.shape[0]):
            row = X[i]
            order = np.argsort(np.where(np.isnan(row), np.inf, row))
            ranks = np.empty_like(order)
            ranks[order] = np.arange(len(row))
            out[i] = ref[ranks]
        return out
    raise ValueError(f"unknown normalize: {normalize!r}")


def _apply_impute(X: np.ndarray, impute: str) -> np.ndarray:
    if impute in ("none", None):
        return X
    if not np.any(np.isnan(X)):
        return X
    if impute == "zero":
        return np.nan_to_num(X, nan=0.0)
    if impute == "min":
        # Per-feature minimum (classic proteomics left-censored imputation).
        col_min = np.nanmin(np.where(np.isnan(X), np.inf, X), axis=0)
        col_min = np.where(np.isfinite(col_min), col_min, 0.0)
        out = X.copy()
        idx = np.where(np.isnan(out))
        out[idx] = np.take(col_min, idx[1])
        return out
    if impute == "mean":
        col_mean = np.nanmean(X, axis=0)
        col_mean = np.where(np.isfinite(col_mean), col_mean, 0.0)
        out = X.copy()
        idx = np.where(np.isnan(out))
        out[idx] = np.take(col_mean, idx[1])
        return out
    raise ValueError(f"unknown impute: {impute!r}")


def load_feature_matrix(
    sample_paths: Sequence[str | Path],
    *,
    kind: str,
    feature_order: Optional[Sequence[str]] = None,
    transform: str = "logcpm",
    normalize: str = "none",
    impute: str = "none",
    missing_fill: float = 0.0,
    min_total: float = 1.0,
) -> Tuple[np.ndarray, List[str], List[str]]:
    """Stack per-sample ``{sample}.{kind}.h5`` into a dense ``samples x features`` matrix.

    ``missing_fill`` is the value used for features absent from a sample before
    transform/normalize/impute (0 for counts; use NaN + impute for abundances).
    Returns (X, feature_ids, sample_ids).
    """
    resolved: List[Tuple[str, Path]] = []
    suffix = f".{kind}.h5"
    for sp in sample_paths:
        h5 = find_feature_h5(sp, kind=kind)
        if h5 is None:
            raise RuntimeError(f"no {kind}.h5 under {sp}")
        sample_id = h5.name[: -len(suffix)]
        resolved.append((sample_id, h5))

    per_sample: List[Tuple[str, List[str], np.ndarray]] = []
    feature_set = set()
    for sample_id, h5 in resolved:
        feats, values = _read_h5(h5)
        per_sample.append((sample_id, feats, values))
        feature_set.update(feats)

    features_out = [str(f) for f in feature_order] if feature_order is not None else sorted(feature_set)
    col_index = {f: j for j, f in enumerate(features_out)}

    n_samples = len(per_sample)
    n_features = len(features_out)
    X = np.full((n_samples, n_features), missing_fill, dtype=np.float64)
    sample_ids: List[str] = []
    for i, (sample_id, feats, values) in enumerate(per_sample):
        sample_ids.append(sample_id)
        for f, v in zip(feats, values):
            j = col_index.get(f)
            if j is not None:
                X[i, j] = v

    X = _apply_transform(X, transform, min_total=min_total)
    X = _apply_normalize(X, normalize)
    X = _apply_impute(X, impute)
    return X.astype(np.float64), features_out, sample_ids
