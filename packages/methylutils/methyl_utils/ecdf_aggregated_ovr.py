"""
Aggregated-feature ECDF one-vs-rest helpers.

This module defines a portable package contract for ECDF OvR classifiers trained
on observed-hybrid aggregated features (gene / structural families) and provides
deterministic training + inference utilities.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from .ecdf_classifier import ECDFClassifier

AGGREGATED_ECDF_OVR_TYPE = "ecdf_aggregated_one_vs_rest"
GENE_ECDF_OVR_TYPE = "ecdf_gene_one_vs_rest"
SUPPORTED_PACKAGE_ECDF_OVR_TYPES = frozenset(
    {AGGREGATED_ECDF_OVR_TYPE, GENE_ECDF_OVR_TYPE}
)
AGGREGATED_ECDF_OVR_VERSION = 1


def _normalize_structural_token(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "promoter_region": "promoter",
        "genebody": "gene_body",
        "body": "gene_body",
        "terminator_region": "terminator",
    }
    token = aliases.get(token, token)
    allowed = {"promoter", "exon", "intron", "gene_body", "terminator"}
    return token if token in allowed else "unknown"


def _safe_numeric(values: Any) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return arr
    return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)


def _normalize_feature_weights(weights: np.ndarray) -> np.ndarray:
    w = _safe_numeric(weights)
    if w.size == 0:
        return w
    w = np.abs(w)
    max_w = float(np.max(w))
    if max_w <= 0.0:
        return np.ones((w.size,), dtype=np.float64)
    out = w / max_w
    out = np.clip(out, 1e-6, 1.0)
    return out


def build_effect_size_feature_weights(
    dmp_df: pd.DataFrame,
    feature_names: Sequence[str],
) -> np.ndarray:
    """
    Deterministic effect-size-aware feature weights for observed-hybrid features.

    - DMP-family features -> mean absolute DMP effect size.
    - Dynamic gene features (`gene::<GENE>`) -> mean absolute effect size over
      loci mapped to that gene.
    - Dynamic structural features (`struct::<GENE>::<FEATURE>`) -> mean absolute
      effect size over loci mapped to that exact `(gene, feature_type)` key.
    - Legacy engineered names are still supported as fallbacks.
    """
    names = [str(x) for x in feature_names]
    if not names:
        return np.zeros((0,), dtype=np.float64)

    work = dmp_df.copy()
    if "effect_size" in work.columns:
        effect = pd.to_numeric(work["effect_size"], errors="coerce").fillna(0.0).astype(float)
    else:
        effect = pd.Series(np.zeros((len(work),), dtype=float))
    abs_eff = np.abs(effect.to_numpy(dtype=np.float64))

    def _masked_mean(mask: np.ndarray) -> float:
        if mask.size != abs_eff.size or not np.any(mask):
            return float(np.mean(abs_eff)) if abs_eff.size else 1.0
        vals = abs_eff[mask]
        if vals.size == 0:
            return float(np.mean(abs_eff)) if abs_eff.size else 1.0
        return float(np.mean(vals))

    default_weight = float(np.mean(abs_eff)) if abs_eff.size else 1.0
    if default_weight <= 0.0:
        default_weight = 1.0

    gene_col = work["gene_name"].astype(str).str.strip() if "gene_name" in work.columns else None
    gene_col_l = gene_col.str.lower() if gene_col is not None else None
    gene_mask = (
        gene_col_l.notna()
        & (gene_col_l != "")
        & (~gene_col_l.isin({"unknown", "nan", "none"}))
    ) if gene_col_l is not None else np.zeros((len(work),), dtype=bool)

    feat_col = work["feature_type"].astype(str).str.strip().str.lower() if "feature_type" in work.columns else None
    feat_col_norm = feat_col.map(_normalize_structural_token) if feat_col is not None else None
    structural_tokens = ("promoter", "exon", "intron", "gene_body", "terminator")
    structural_masks: Dict[str, np.ndarray] = {}
    if feat_col is not None and feat_col_norm is not None:
        for token in structural_tokens:
            contains_mask = feat_col.str.contains(token, regex=False, na=False)
            canonical_mask = feat_col_norm == token
            structural_masks[token] = (contains_mask | canonical_mask).to_numpy(dtype=bool)
    else:
        for token in structural_tokens:
            structural_masks[token] = np.zeros((len(work),), dtype=bool)

    gene_weight = _masked_mean(np.asarray(gene_mask, dtype=bool))
    structural_weight_map = {token: _masked_mean(mask) for token, mask in structural_masks.items()}

    def _gene_key_mask(gene_key: str) -> np.ndarray:
        if gene_col is None:
            return np.zeros((len(work),), dtype=bool)
        g = str(gene_key).strip()
        return (gene_col == g).to_numpy(dtype=bool)

    def _struct_key_mask(gene_key: str, feature_key: str) -> np.ndarray:
        if gene_col is None or feat_col_norm is None:
            return np.zeros((len(work),), dtype=bool)
        g = str(gene_key).strip()
        f = _normalize_structural_token(feature_key)
        return ((gene_col == g) & (feat_col_norm == f)).to_numpy(dtype=bool)

    out = np.zeros((len(names),), dtype=np.float64)
    for i, name in enumerate(names):
        lname = name.strip().lower()
        if lname.startswith("gene::"):
            raw_gene = name.split("::", 1)[1] if "::" in name else ""
            out[i] = _masked_mean(_gene_key_mask(raw_gene))
            continue
        if lname.startswith("struct::"):
            parts = name.split("::", 2)
            if len(parts) == 3:
                out[i] = _masked_mean(_struct_key_mask(parts[1], parts[2]))
            else:
                out[i] = default_weight
            continue
        if lname.startswith("gene_"):
            out[i] = gene_weight
            continue
        if lname.startswith("struct_"):
            matched = None
            for token in structural_tokens:
                if f"struct_{token}_" in lname:
                    matched = token
                    break
            out[i] = structural_weight_map.get(matched or "", default_weight)
            continue
        out[i] = default_weight

    return _normalize_feature_weights(out)


def fit_feature_scaler(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Fit per-feature fill value and min/max transform for [0,1] ECDF domain.
    """
    Xf = np.asarray(X, dtype=np.float64)
    if Xf.ndim != 2:
        raise ValueError("X must be 2D")
    n_features = int(Xf.shape[1])
    if n_features <= 0:
        return (
            np.zeros((0,), dtype=np.float64),
            np.zeros((0,), dtype=np.float64),
            np.ones((0,), dtype=np.float64),
        )

    fill_values = np.zeros((n_features,), dtype=np.float64)
    mins = np.zeros((n_features,), dtype=np.float64)
    maxs = np.ones((n_features,), dtype=np.float64)
    for j in range(n_features):
        col = np.asarray(Xf[:, j], dtype=np.float64)
        finite = np.isfinite(col)
        if not np.any(finite):
            fill_values[j] = 0.5
            mins[j] = 0.0
            maxs[j] = 1.0
            continue
        vals = col[finite]
        fill_values[j] = float(np.median(vals))
        lo = float(np.min(vals))
        hi = float(np.max(vals))
        if not np.isfinite(lo):
            lo = 0.0
        if not np.isfinite(hi):
            hi = lo + 1.0
        if hi - lo < 1e-9:
            hi = lo + 1.0
        mins[j] = lo
        maxs[j] = hi
    return fill_values, mins, maxs


def transform_features_to_unit_interval(
    X: np.ndarray,
    *,
    fill_values: np.ndarray,
    mins: np.ndarray,
    maxs: np.ndarray,
) -> np.ndarray:
    Xf = np.asarray(X, dtype=np.float64)
    if Xf.ndim != 2:
        raise ValueError("X must be 2D")
    out = np.array(Xf, dtype=np.float64, copy=True)
    n_features = int(out.shape[1])
    if n_features != int(fill_values.shape[0]) or n_features != int(mins.shape[0]) or n_features != int(maxs.shape[0]):
        raise ValueError(
            f"Feature transform mismatch: X has {n_features} columns, "
            f"fill/min/max lengths are {fill_values.shape[0]}/{mins.shape[0]}/{maxs.shape[0]}"
        )
    for j in range(n_features):
        col = out[:, j]
        finite = np.isfinite(col)
        if not np.all(finite):
            col = np.where(finite, col, float(fill_values[j]))
        lo = float(mins[j])
        hi = float(maxs[j])
        den = hi - lo
        if den <= 1e-12:
            col = np.full_like(col, 0.5, dtype=np.float64)
        else:
            col = (col - lo) / den
        out[:, j] = np.clip(col, 1e-7, 1.0 - 1e-7)
    return out


def _build_hist_counts(X_scaled: np.ndarray, y_bin: np.ndarray, n_bins: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_samples, n_features = X_scaled.shape
    if n_features <= 0:
        raise ValueError("Aggregated ECDF requires at least one feature")
    if n_samples <= 0:
        raise ValueError("Aggregated ECDF requires at least one sample")
    bin_edges = np.linspace(0.0, 1.0, int(n_bins) + 1, dtype=np.float64)
    n_intervals = len(bin_edges) - 1
    c0 = np.zeros((n_features, n_intervals), dtype=np.float64)
    c1 = np.zeros((n_features, n_intervals), dtype=np.float64)

    yb = np.asarray(y_bin, dtype=np.int32).reshape(-1)
    if yb.shape[0] != n_samples:
        raise ValueError("y_bin length mismatch")

    mask_pos = yb == 1
    mask_neg = yb == 0
    if not np.any(mask_pos) or not np.any(mask_neg):
        raise ValueError("OvR head requires both positive and negative samples")

    for j in range(n_features):
        xp = X_scaled[mask_pos, j]
        xn = X_scaled[mask_neg, j]
        c1[j, :], _ = np.histogram(np.clip(xp, 0.0, 1.0), bins=bin_edges)
        c0[j, :], _ = np.histogram(np.clip(xn, 0.0, 1.0), bins=bin_edges)
    return bin_edges, c0, c1


def train_aggregated_ecdf_ovr_package(
    X: np.ndarray,
    y: Sequence[int],
    *,
    class_names: Sequence[str],
    feature_names: Sequence[str],
    feature_weights: np.ndarray,
    feature_family_set: str,
    feature_mode: str,
    package_metadata: Dict[str, Any] | None = None,
    n_bins: int = 100,
    temperature: float = 2.0,
    classifier_type: str = AGGREGATED_ECDF_OVR_TYPE,
) -> Dict[str, Any]:
    y_arr = np.asarray(y, dtype=np.int32).reshape(-1)
    X_arr = np.asarray(X, dtype=np.float64)
    if X_arr.ndim != 2:
        raise ValueError("X must be 2D")
    if X_arr.shape[0] != y_arr.shape[0]:
        raise ValueError("X/y sample count mismatch")
    if X_arr.shape[1] != len(feature_names):
        raise ValueError("feature_names length must match X columns")
    if len(class_names) < 2:
        raise ValueError("At least 2 classes required")

    fill_values, mins, maxs = fit_feature_scaler(X_arr)
    X_scaled = transform_features_to_unit_interval(
        X_arr,
        fill_values=fill_values,
        mins=mins,
        maxs=maxs,
    )

    w = _normalize_feature_weights(np.asarray(feature_weights, dtype=np.float64).reshape(-1))
    if w.shape[0] != X_scaled.shape[1]:
        raise ValueError("feature_weights length mismatch")

    entries: List[Dict[str, Any]] = []
    for class_idx, class_label in enumerate(class_names):
        y_bin = (y_arr == int(class_idx)).astype(np.int32)
        bin_edges, c0, c1 = _build_hist_counts(X_scaled, y_bin, int(n_bins))
        ecdf = ECDFClassifier(
            positions=np.arange(X_scaled.shape[1], dtype=np.uint32),
            bin_edges=bin_edges,
            bin_counts_c1=c0,
            bin_counts_c2=c1,
            weights=w,
            directions=np.ones((X_scaled.shape[1],), dtype=np.int8),
            temperature=float(temperature),
            contexts=np.asarray(["agg"] * X_scaled.shape[1], dtype=object),
            prior_c1=float(max(int(np.sum(y_bin == 0)), 1)),
            prior_c2=float(max(int(np.sum(y_bin == 1)), 1)),
        )
        entries.append(
            {
                "positive_class_index": int(class_idx),
                "positive_class_label": str(class_label),
                "feature_indices": list(range(X_scaled.shape[1])),
                "ecdf": ecdf,
                "n_positive": int(np.sum(y_bin == 1)),
                "n_negative": int(np.sum(y_bin == 0)),
            }
        )

    metadata = dict(package_metadata or {})
    metadata.setdefault("feature_mode", str(feature_mode))
    metadata.setdefault("feature_family_set", str(feature_family_set))
    metadata.setdefault("n_samples", int(X_arr.shape[0]))
    metadata.setdefault("n_features", int(X_arr.shape[1]))
    metadata.setdefault("n_classes", int(len(class_names)))

    resolved_type = str(classifier_type or AGGREGATED_ECDF_OVR_TYPE)
    if resolved_type not in SUPPORTED_PACKAGE_ECDF_OVR_TYPES:
        raise ValueError(
            f"Unsupported classifier_type {resolved_type!r}; "
            f"expected one of {sorted(SUPPORTED_PACKAGE_ECDF_OVR_TYPES)}"
        )

    return {
        "classifier_type": resolved_type,
        "package_version": AGGREGATED_ECDF_OVR_VERSION,
        "class_names": [str(x) for x in class_names],
        "feature_schema": {
            "feature_names": [str(x) for x in feature_names],
            "feature_family_set": str(feature_family_set),
            "feature_mode": str(feature_mode),
            "transform": "fill_then_minmax_clip_0_1",
            "fill_values": [float(v) for v in fill_values.tolist()],
            "mins": [float(v) for v in mins.tolist()],
            "maxs": [float(v) for v in maxs.tolist()],
            "feature_weights": [float(v) for v in w.tolist()],
            "weighting": "effect_size_aware_deterministic",
        },
        "binary_models": entries,
        "metadata": metadata,
    }


def predict_aggregated_ecdf_ovr_proba(
    package: Dict[str, Any],
    X_raw: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Score raw aggregated features with an aggregated ECDF OvR package.

    Returns:
        probs: (n_samples, n_classes) posterior probabilities.
        evidence_logits: (n_samples, n_classes) OvR evidence before softmax.
    """
    if str(package.get("classifier_type")) not in SUPPORTED_PACKAGE_ECDF_OVR_TYPES:
        raise ValueError(f"Unsupported classifier_type: {package.get('classifier_type')!r}")
    class_names = [str(x) for x in (package.get("class_names") or [])]
    if len(class_names) < 2:
        raise ValueError("Aggregated package missing class_names")
    schema = package.get("feature_schema") or {}
    feature_names = [str(x) for x in (schema.get("feature_names") or [])]
    fill_values = _safe_numeric(schema.get("fill_values") or [])
    mins = _safe_numeric(schema.get("mins") or [])
    maxs = _safe_numeric(schema.get("maxs") or [])
    X = np.asarray(X_raw, dtype=np.float64)
    if X.ndim != 2:
        raise ValueError("X_raw must be 2D")
    if X.shape[1] != len(feature_names):
        raise ValueError(
            f"Aggregated feature schema mismatch: expected {len(feature_names)} columns, got {X.shape[1]}"
        )
    X_scaled = transform_features_to_unit_interval(
        X,
        fill_values=fill_values,
        mins=mins,
        maxs=maxs,
    )
    entries = package.get("binary_models") or []
    if len(entries) != len(class_names):
        raise ValueError("binary_models length must equal class_names length")

    n_samples = X_scaled.shape[0]
    n_classes = len(class_names)
    logits = np.zeros((n_samples, n_classes), dtype=np.float64)
    for entry in entries:
        pos_idx = int(entry.get("positive_class_index", -1))
        if pos_idx < 0 or pos_idx >= n_classes:
            raise ValueError(f"Invalid positive_class_index in binary model: {pos_idx}")
        ecdf = entry.get("ecdf")
        if ecdf is None or not hasattr(ecdf, "predict_proba"):
            raise ValueError(f"binary model {pos_idx} missing ECDF classifier")
        cols = np.asarray(entry.get("feature_indices") or [], dtype=np.int32)
        if cols.size == 0:
            cols = np.arange(X_scaled.shape[1], dtype=np.int32)
        probs_bin = ecdf.predict_proba(X_scaled[:, cols], availability_mask=None, debug=False)
        if probs_bin.ndim != 2 or probs_bin.shape[1] != 2:
            raise ValueError(f"binary model {pos_idx} returned invalid probability shape")
        p_neg = np.clip(probs_bin[:, 0], 1e-12, 1.0)
        p_pos = np.clip(probs_bin[:, 1], 1e-12, 1.0)
        logits[:, pos_idx] = np.log(p_pos) - np.log(p_neg)

    logits = np.nan_to_num(logits, nan=0.0, posinf=20.0, neginf=-20.0)
    logits = np.clip(logits, -40.0, 40.0)
    # Preserve raw OvR evidence for diagnostics before softmax stabilization.
    evidence_logits = np.asarray(logits, dtype=np.float64).copy()
    logits_stable = logits - np.max(logits, axis=1, keepdims=True)
    probs = np.exp(logits_stable)
    denom = np.sum(probs, axis=1, keepdims=True)
    denom = np.where(denom <= 0.0, 1.0, denom)
    probs = probs / denom
    return probs.astype(np.float64), evidence_logits.astype(np.float64)
