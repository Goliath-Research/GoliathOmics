"""
Bootstrap distribution of held-out QC metrics from per-sample predictions.

This module is the statistical core of the true held-out batch evaluation
(Workflow 3). Given a *frozen* model's per-sample predictions on a hold-out
batch that was never used for training or model selection, it characterizes the
sampling distribution of the standard classification quality metrics by
resampling (bootstrapping) the per-sample results.

Why bootstrap: the frozen model applied to a fixed hold-out set yields a single
point estimate per metric. To report a *distribution* (mean, standard deviation,
confidence interval, percentiles) of balanced accuracy, sensitivity,
specificity, F1, and ROC-AUC, we resample the scored samples with replacement.
This is standard bootstrap interval reporting for classification metrics on a
fixed scored hold-out.

The functions here depend only on ``numpy`` and ``scikit-learn`` so they can be
unit-tested in isolation from the pipeline orchestration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    recall_score,
    roc_auc_score,
)

# Metric keys emitted for a binary (K=2) problem.
BINARY_METRIC_KEYS: Tuple[str, ...] = (
    "accuracy",
    "balanced_accuracy",
    "sensitivity",
    "specificity",
    "f1",
    "auc",
    "macro_f1",
)

# Metric keys emitted for a multiclass (K>=3) problem. Screening keys treat the
# control class vs the pooled disease classes as a binary detection problem.
MULTICLASS_METRIC_KEYS: Tuple[str, ...] = (
    "accuracy",
    "balanced_accuracy",
    "macro_recall",
    "macro_specificity",
    "macro_f1",
    "auc",
    "screening_sensitivity",
    "screening_specificity",
    "screening_f1",
    "screening_auc",
)


def _nan() -> float:
    return float("nan")


def _ovr_specificity(cm: np.ndarray, class_index: int) -> float:
    tp = float(cm[class_index, class_index])
    fp = float(cm[:, class_index].sum() - tp)
    fn = float(cm[class_index, :].sum() - tp)
    tn = float(cm.sum() - tp - fp - fn)
    denom = tn + fp
    return float(tn / denom) if denom > 0 else _nan()


def _binary_screening_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: Optional[np.ndarray],
    *,
    control_index: int,
    disease_indices: Sequence[int],
) -> Dict[str, float]:
    """Control vs pooled-disease detection metrics (used for multiclass screening)."""
    disease_set = {int(i) for i in disease_indices}
    yt = np.where(y_true == control_index, 0, 1).astype(int)
    yp = np.isin(y_pred, list(disease_set)).astype(int)
    cm = confusion_matrix(yt, yp, labels=[0, 1])
    if cm.shape != (2, 2):
        return {"screening_sensitivity": _nan(), "screening_specificity": _nan(), "screening_f1": _nan(), "screening_auc": _nan()}
    tn, fp, fn, tp = (int(v) for v in cm.ravel().tolist())
    sens = float(tp / (tp + fn)) if (tp + fn) > 0 else _nan()
    spec = float(tn / (tn + fp)) if (tn + fp) > 0 else _nan()
    f1_denom = (2 * tp) + fp + fn
    f1 = float((2 * tp) / f1_denom) if f1_denom > 0 else _nan()
    auc = _nan()
    if y_proba is not None:
        try:
            # Pooled-disease score = 1 - P(control).
            disease_score = 1.0 - y_proba[:, control_index]
            if len(np.unique(yt)) == 2:
                auc = float(roc_auc_score(yt, disease_score))
        except Exception:
            auc = _nan()
    return {
        "screening_sensitivity": sens,
        "screening_specificity": spec,
        "screening_f1": f1,
        "screening_auc": auc,
    }


def compute_metrics_once(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_proba: Optional[np.ndarray] = None,
    *,
    n_classes: int,
    control_index: int = 0,
    disease_indices: Optional[Sequence[int]] = None,
) -> Dict[str, float]:
    """
    Compute one set of QC metrics from per-sample labels/predictions/probabilities.

    ``y_proba`` (n_samples, n_classes) is required for AUC; when absent or when a
    resample lacks both classes, AUC is reported as NaN and excluded from summaries.
    """
    y_true = np.asarray(y_true, dtype=int).reshape(-1)
    y_pred = np.asarray(y_pred, dtype=int).reshape(-1)
    labels = list(range(int(n_classes)))
    if disease_indices is None:
        disease_indices = [i for i in labels if i != int(control_index)]
    disease_indices = [int(i) for i in disease_indices]

    out: Dict[str, float] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
    }
    cm = confusion_matrix(y_true, y_pred, labels=labels)

    if int(n_classes) == 2:
        pos = disease_indices[0] if disease_indices else 1
        ctrl = int(control_index)
        # Sensitivity = recall of positive (disease); specificity = recall of control.
        out["sensitivity"] = float(recall_score(y_true, y_pred, labels=[pos], average="macro", zero_division=0))
        out["specificity"] = float(recall_score(y_true, y_pred, labels=[ctrl], average="macro", zero_division=0))
        out["f1"] = float(f1_score(y_true, y_pred, labels=[pos], average="macro", zero_division=0))
        out["auc"] = _nan()
        if y_proba is not None:
            try:
                yt_bin = (y_true == pos).astype(int)
                if len(np.unique(yt_bin)) == 2:
                    out["auc"] = float(roc_auc_score(yt_bin, y_proba[:, pos]))
            except Exception:
                out["auc"] = _nan()
        return out

    # Multiclass.
    out["macro_recall"] = float(recall_score(y_true, y_pred, labels=labels, average="macro", zero_division=0))
    spec_vals = [s for s in (_ovr_specificity(cm, i) for i in labels) if not np.isnan(s)]
    out["macro_specificity"] = float(np.mean(spec_vals)) if spec_vals else _nan()
    out["auc"] = _nan()
    if y_proba is not None:
        try:
            if len(np.unique(y_true)) == int(n_classes):
                out["auc"] = float(
                    roc_auc_score(y_true, y_proba, multi_class="ovr", average="macro", labels=labels)
                )
        except Exception:
            out["auc"] = _nan()
    out.update(
        _binary_screening_metrics(
            y_true, y_pred, y_proba, control_index=int(control_index), disease_indices=disease_indices
        )
    )
    return out


def _resample_indices(
    y_true: np.ndarray,
    rng: np.random.Generator,
    *,
    stratified: bool,
) -> np.ndarray:
    n = len(y_true)
    if not stratified:
        return rng.integers(0, n, size=n)
    parts: List[np.ndarray] = []
    for cls in np.unique(y_true):
        cls_idx = np.where(y_true == cls)[0]
        if cls_idx.size == 0:
            continue
        parts.append(rng.choice(cls_idx, size=cls_idx.size, replace=True))
    if not parts:
        return rng.integers(0, n, size=n)
    return np.concatenate(parts)


def _summarize(values: np.ndarray, *, ci: float) -> Dict[str, Any]:
    finite = values[np.isfinite(values)]
    n_valid = int(finite.size)
    if n_valid == 0:
        return {"mean": None, "std": None, "ci_low": None, "ci_high": None, "n_valid": 0, "percentiles": {}}
    alpha = (1.0 - float(ci)) / 2.0
    lo_pct = 100.0 * alpha
    hi_pct = 100.0 * (1.0 - alpha)
    pcts = [5, 25, 50, 75, 95]
    return {
        "mean": float(np.mean(finite)),
        "std": float(np.std(finite, ddof=1)) if n_valid > 1 else 0.0,
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
        "ci_level": float(ci),
        "ci_low": float(np.percentile(finite, lo_pct)),
        "ci_high": float(np.percentile(finite, hi_pct)),
        "n_valid": n_valid,
        "percentiles": {f"p{p}": float(np.percentile(finite, p)) for p in pcts},
    }


def bootstrap_holdout_metrics(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    y_proba: Optional[np.ndarray] = None,
    *,
    n_classes: Optional[int] = None,
    control_index: int = 0,
    disease_indices: Optional[Sequence[int]] = None,
    n_bootstrap: int = 1000,
    ci: float = 0.95,
    seed: Optional[int] = None,
    stratified: bool = True,
) -> Dict[str, Any]:
    """
    Point estimate plus bootstrap distribution of held-out QC metrics.

    Returns a JSON-serializable dict with:
      - ``point``: metrics on the full hold-out set,
      - ``bootstrap``: per-metric {mean, std, ci_low, ci_high, percentiles, n_valid},
      - ``samples``: list of per-resample metric dicts (for CSV export / plotting).
    """
    yt = np.asarray(y_true, dtype=int).reshape(-1)
    yp = np.asarray(y_pred, dtype=int).reshape(-1)
    if yt.shape != yp.shape:
        raise ValueError(f"y_true and y_pred length mismatch: {yt.shape} vs {yp.shape}")
    if yt.size == 0:
        raise ValueError("Cannot bootstrap an empty hold-out set")
    proba = None
    if y_proba is not None:
        proba = np.asarray(y_proba, dtype=float)
        if proba.ndim != 2 or proba.shape[0] != yt.size:
            raise ValueError(f"y_proba must be (n_samples, n_classes); got {proba.shape}")
    if n_classes is None:
        n_classes = int(max(int(yt.max()), int(yp.max()), (proba.shape[1] - 1) if proba is not None else 0)) + 1
    n_classes = int(n_classes)
    if disease_indices is None:
        disease_indices = [i for i in range(n_classes) if i != int(control_index)]
    disease_indices = [int(i) for i in disease_indices]

    metric_keys = BINARY_METRIC_KEYS if n_classes == 2 else MULTICLASS_METRIC_KEYS

    point = compute_metrics_once(
        yt, yp, proba, n_classes=n_classes, control_index=control_index, disease_indices=disease_indices
    )

    rng = np.random.default_rng(seed)
    per_resample: List[Dict[str, float]] = []
    n_bootstrap = max(1, int(n_bootstrap))
    for _ in range(n_bootstrap):
        idx = _resample_indices(yt, rng, stratified=stratified)
        pr = proba[idx] if proba is not None else None
        m = compute_metrics_once(
            yt[idx], yp[idx], pr, n_classes=n_classes, control_index=control_index, disease_indices=disease_indices
        )
        per_resample.append({k: float(m.get(k, _nan())) for k in metric_keys})

    bootstrap: Dict[str, Any] = {}
    for k in metric_keys:
        vals = np.array([r[k] for r in per_resample], dtype=float)
        bootstrap[k] = _summarize(vals, ci=ci)

    return {
        "n_samples": int(yt.size),
        "n_classes": n_classes,
        "n_bootstrap": n_bootstrap,
        "ci_level": float(ci),
        "stratified": bool(stratified),
        "seed": seed,
        "control_index": int(control_index),
        "disease_indices": disease_indices,
        "metric_keys": list(metric_keys),
        "point": {k: (float(point[k]) if k in point and np.isfinite(point[k]) else None) for k in metric_keys},
        "bootstrap": bootstrap,
        "samples": per_resample,
    }
