"""
Unified classification metrics for validation backends.

Binary projects emit sensitivity/specificity for the disease vs control pair.
Multiclass projects emit per-class OvR metrics, explicit macro summaries, and an
optional screening_binary block (control vs pooled disease).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)

VALIDATION_METRICS_SCHEMA_VERSION = "probabilistic_v3_mc_v1"


def _heuristic_control_class_index(class_names: Sequence[str]) -> int:
    if not class_names:
        return 0
    normalized = [str(x).strip().lower() for x in class_names]
    for name in ("healthy", "control", "normal", "all"):
        if name in normalized:
            return int(normalized.index(name))
    for idx, name in enumerate(normalized):
        if any(token in name for token in ("healthy", "control", "normal")):
            return int(idx)
    return 0


def resolve_class_roles(project: Any) -> Dict[str, Any]:
    """
    Resolve class order and control/disease indices from a pipeline project.

    Prefer ``_get_resolved_groups_with_side()`` when control/disease config exists.
    """
    class_names: List[str] = []
    class_sides: List[Dict[str, Any]] = []
    control_indices: List[int] = []
    disease_indices: List[int] = []

    get_with_side = getattr(project, "_get_resolved_groups_with_side", None)
    if callable(get_with_side):
        try:
            with_side = list(get_with_side())
        except Exception:
            with_side = []
        if with_side:
            for idx, (label, _paths, side) in enumerate(with_side):
                name = str(label)
                class_names.append(name)
                side_norm = str(side).strip().lower()
                class_sides.append(
                    {
                        "class_index": int(idx),
                        "class_name": name,
                        "side": side_norm if side_norm in ("control", "disease") else "unknown",
                    }
                )
                if side_norm == "control":
                    control_indices.append(int(idx))
                elif side_norm == "disease":
                    disease_indices.append(int(idx))

    if not class_names:
        resolved = list(project.get_resolved_groups())
        class_names = [str(lbl) for lbl, _ in resolved]
        control_idx = _heuristic_control_class_index(class_names)
        control_indices = [int(control_idx)]
        disease_indices = [i for i in range(len(class_names)) if i != control_idx]
        class_sides = [
            {
                "class_index": int(i),
                "class_name": class_names[i],
                "side": "control" if int(i) == control_idx else "disease",
            }
            for i in range(len(class_names))
        ]

    if not control_indices:
        control_idx = _heuristic_control_class_index(class_names)
        control_indices = [int(control_idx)]
        disease_indices = [i for i in range(len(class_names)) if i not in control_indices]

    control_class_index = int(control_indices[0])
    n_classes = len(class_names)
    if n_classes == 2:
        positive_class_index = int(disease_indices[0]) if disease_indices else 1
    else:
        positive_class_index = int(disease_indices[0]) if disease_indices else 1

    return {
        "class_names": class_names,
        "control_class_index": control_class_index,
        "positive_class_index": positive_class_index,
        "disease_class_indices": [int(i) for i in disease_indices],
        "class_sides": class_sides,
    }


def select_healthy_index_for_labels(class_names: Sequence[str]) -> int:
    """Backward-compatible alias for control class index from label heuristics."""
    return _heuristic_control_class_index(class_names)


def _per_class_specificity_ovr(cm: np.ndarray, class_index: int) -> float:
    tp = float(cm[class_index, class_index])
    fp = float(cm[:, class_index].sum() - tp)
    fn = float(cm[class_index, :].sum() - tp)
    tn = float(cm.sum() - tp - fp - fn)
    denom = tn + fp
    return float(tn / denom) if denom > 0 else 0.0


def _binary_metrics_from_counts(tp: int, tn: int, fp: int, fn: int) -> Dict[str, Any]:
    sens_denom = tp + fn
    spec_denom = tn + fp
    ppv_denom = tp + fp
    npv_denom = tn + fn
    sensitivity = float(tp / sens_denom) if sens_denom > 0 else 0.0
    specificity = float(tn / spec_denom) if spec_denom > 0 else 0.0
    precision = float(tp / ppv_denom) if ppv_denom > 0 else 0.0
    npv = float(tn / npv_denom) if npv_denom > 0 else 0.0
    f1_denom = (2 * tp) + fp + fn
    f1 = float((2 * tp) / f1_denom) if f1_denom > 0 else 0.0
    return {
        "confusion_matrix": [[int(tn), int(fp)], [int(fn), int(tp)]],
        "sensitivity": sensitivity,
        "specificity": specificity,
        "precision": precision,
        "npv": npv,
        "f1": f1,
        "n_samples": int(tp + tn + fp + fn),
    }


def _build_screening_binary(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    control_class_index: int,
    disease_class_indices: Sequence[int],
) -> Dict[str, Any]:
    disease_set = {int(i) for i in disease_class_indices}
    yt = np.asarray(y_true, dtype=int).reshape(-1)
    yp = np.asarray(y_pred, dtype=int).reshape(-1)
    ctrl = int(control_class_index)
    y_true_bin = np.where(yt == ctrl, 0, 1).astype(int)
    y_pred_bin = np.isin(yp, list(disease_set)).astype(int)

    cm = confusion_matrix(y_true_bin, y_pred_bin, labels=[0, 1])
    if cm.shape != (2, 2):
        tn = fp = fn = tp = 0
    else:
        tn, fp, fn, tp = [int(v) for v in cm.ravel().tolist()]
    out = _binary_metrics_from_counts(tp, tn, fp, fn)
    out["definition"] = "control_vs_pooled_disease"
    out["control_class_index"] = int(control_class_index)
    out["disease_class_indices"] = [int(i) for i in disease_class_indices]
    return out


def _normalize_class_roles(
    class_names: Sequence[str],
    class_roles: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    names = [str(x) for x in class_names]
    if class_roles is not None:
        control = int(class_roles.get("control_class_index", 0))
        disease = list(class_roles.get("disease_class_indices") or [])
        if not disease:
            disease = [i for i in range(len(names)) if i != control]
        positive = int(class_roles.get("positive_class_index", disease[0] if disease else 1))
        sides = class_roles.get("class_sides")
        if not isinstance(sides, list):
            sides = [
                {
                    "class_index": i,
                    "class_name": names[i],
                    "side": "control" if i == control else "disease",
                }
                for i in range(len(names))
            ]
        return {
            "class_names": names,
            "control_class_index": control,
            "positive_class_index": positive,
            "disease_class_indices": [int(i) for i in disease],
            "class_sides": sides,
        }
    control = _heuristic_control_class_index(names)
    disease = [i for i in range(len(names)) if i != control]
    return {
        "class_names": names,
        "control_class_index": int(control),
        "positive_class_index": int(disease[0]) if disease else 1,
        "disease_class_indices": [int(i) for i in disease],
        "class_sides": [
            {
                "class_index": i,
                "class_name": names[i],
                "side": "control" if i == control else "disease",
            }
            for i in range(len(names))
        ],
    }


def compute_validation_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    class_names: Sequence[str],
    *,
    class_roles: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute JSON-serializable validation metrics for binary or multiclass problems.
    """
    roles = _normalize_class_roles(class_names, class_roles)
    names = roles["class_names"]
    n_classes = len(names)
    labels = list(range(n_classes))
    yt = np.asarray(y_true, dtype=int).reshape(-1)
    yp = np.asarray(y_pred, dtype=int).reshape(-1)

    precision, recall, f1, support = precision_recall_fscore_support(
        yt, yp, labels=labels, zero_division=0
    )
    cm = confusion_matrix(yt, yp, labels=labels)

    per_class: List[Dict[str, Any]] = []
    specificity_vals: List[float] = []
    for i in range(n_classes):
        spec_ovr = _per_class_specificity_ovr(cm, i)
        specificity_vals.append(spec_ovr)
        per_class.append(
            {
                "class_index": int(i),
                "class_name": names[i] if i < len(names) else f"Class_{i}",
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "f1": float(f1[i]),
                "specificity_ovr": spec_ovr,
                "support": int(support[i]),
            }
        )

    macro_precision = float(np.mean(precision)) if len(precision) > 0 else 0.0
    macro_recall = float(np.mean(recall)) if len(recall) > 0 else 0.0
    macro_f1 = float(np.mean(f1)) if len(f1) > 0 else 0.0
    macro_specificity = float(np.mean(specificity_vals)) if specificity_vals else 0.0
    weighted_f1 = float(np.average(f1, weights=support) if support.sum() > 0 else 0.0)

    metrics: Dict[str, Any] = {
        "metrics_schema_version": VALIDATION_METRICS_SCHEMA_VERSION,
        "accuracy": float(accuracy_score(yt, yp)),
        "balanced_accuracy": float(balanced_accuracy_score(yt, yp)),
        "confusion_matrix": cm.tolist(),
        "n_samples": int(len(yt)),
        "n_classes": int(n_classes),
        "class_names": names,
        "class_roles": {
            "control_class_index": int(roles["control_class_index"]),
            "positive_class_index": int(roles["positive_class_index"]),
            "disease_class_indices": list(roles["disease_class_indices"]),
            "class_sides": roles["class_sides"],
        },
        "per_class": per_class,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "macro_specificity": macro_specificity,
        "weighted_f1": weighted_f1,
    }

    if n_classes == 2:
        pos_idx = int(roles["positive_class_index"])
        ctrl_idx = int(roles["control_class_index"])
        metrics["sensitivity"] = float(recall[pos_idx])
        metrics["specificity"] = float(recall[ctrl_idx])
        metrics["precision_binary"] = float(precision[pos_idx])
        metrics["recall_binary"] = float(recall[pos_idx])
        metrics["f1_binary"] = float(f1[pos_idx])
    elif n_classes >= 3:
        metrics["screening_binary"] = _build_screening_binary(
            yt,
            yp,
            control_class_index=int(roles["control_class_index"]),
            disease_class_indices=roles["disease_class_indices"],
        )

    return metrics


CLASSIFICATION_RESULTS_FILENAME = "classification_results.csv"


def classifier_comparison_output_dir(project: Any, classifier_root: Path) -> Path:
    """Return ``classifiers/<control>/<disease>`` when comparisons exist, else ``classifier_root``."""
    root = Path(classifier_root)
    comparisons = project.get_comparisons()
    if comparisons:
        spec = comparisons[0]
        return root / str(spec.control_group) / str(spec.disease_group)
    return root


def write_classification_results_csv(
    path: Path,
    *,
    sample_paths: Sequence[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    probs: np.ndarray,
) -> Path:
    """Write per-sample train/test classification rows (tabular/generative backend layout)."""
    import csv

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    yt = np.asarray(y_true, dtype=int).reshape(-1)
    yp = np.asarray(y_pred, dtype=int).reshape(-1)
    pr = np.asarray(probs, dtype=float)
    if pr.ndim != 2:
        raise ValueError(f"probs must be 2D, got shape {pr.shape}")
    if not (len(sample_paths) == len(yt) == len(yp) == pr.shape[0]):
        raise ValueError(
            "sample_paths, y_true, y_pred, and probs rows must have the same length "
            f"({len(sample_paths)}, {len(yt)}, {len(yp)}, {pr.shape[0]})"
        )
    fieldnames = ["sample", "sample_path", "expected_class", "prediction"] + [
        f"prob_class{j}" for j in range(pr.shape[1])
    ]
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, sample_path in enumerate(sample_paths):
            row = {
                "sample": Path(str(sample_path)).name,
                "sample_path": str(sample_path),
                "expected_class": int(yt[i]),
                "prediction": int(yp[i]),
            }
            for j in range(pr.shape[1]):
                row[f"prob_class{j}"] = float(pr[i, j])
            writer.writerow(row)
    return out
