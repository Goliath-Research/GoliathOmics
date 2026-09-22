"""
Read validation_metrics.json, flatten to one row per run, and compute empirical
distribution summaries under a versioned metrics schema.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .classification_metrics import VALIDATION_METRICS_SCHEMA_VERSION

# Schema version for aggregated Monte Carlo metrics artifacts.
METRICS_SCHEMA_VERSION = VALIDATION_METRICS_SCHEMA_VERSION

# Scalar metric keys we want in the flat table and in the summary
# (skip nested per_class, confusion_matrix, screening_binary, class_roles).
SCALAR_KEYS = [
    "accuracy",
    "balanced_accuracy",
    "nll",
    "brier_score",
    "ece",
    "sensitivity",
    "specificity",
    "macro_precision",
    "macro_recall",
    "macro_specificity",
    "macro_f1",
    "weighted_f1",
    "precision_binary",
    "recall_binary",
    "f1_binary",
    "screening_sensitivity",
    "screening_specificity",
    "screening_precision",
    "screening_npv",
    "screening_f1",
    "n_samples",
    "n_classes",
]

_SCREENING_SCALAR_MAP = {
    "sensitivity": "screening_sensitivity",
    "specificity": "screening_specificity",
    "precision": "screening_precision",
    "npv": "screening_npv",
    "f1": "screening_f1",
}


def metrics_schema_descriptor() -> Dict[str, Any]:
    """
    Return a compact descriptor for the MC metric schema written by this module.
    """
    return {
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "scalar_metric_keys": list(SCALAR_KEYS),
        "notes": (
            "Flat tables include scalar top-level keys, per-class recall columns "
            "(recall_<class_name>), and optional training_/holdout_ prefixed variants "
            "when evaluation_semantics=train_holdout."
        ),
    }


def _sanitize_class_label(class_name: str, class_index: int) -> str:
    raw = str(class_name).strip() if class_name else ""
    sanitized = re.sub(r"[^\w]+", "_", raw).strip("_")
    if not sanitized:
        sanitized = f"class_{class_index}"
    return sanitized


def _recall_column_name(class_index: int, class_name: str) -> str:
    return f"recall_{_sanitize_class_label(class_name, class_index)}"


def _flatten_per_class_recall(
    metrics: Dict[str, Any],
    out: Dict[str, Any],
    *,
    key_prefix: str = "",
) -> None:
    per_class = metrics.get("per_class")
    if not isinstance(per_class, list):
        return
    used: set[str] = set()
    for i, row in enumerate(per_class):
        if not isinstance(row, dict):
            continue
        recall = row.get("recall")
        if not isinstance(recall, (int, float)):
            continue
        class_index = row.get("class_index")
        if not isinstance(class_index, int):
            class_index = i
        class_name = str(row.get("class_name", f"class_{class_index}"))
        col = f"{key_prefix}{_recall_column_name(class_index, class_name)}"
        if col in used:
            col = f"{key_prefix}recall_{_sanitize_class_label(class_name, class_index)}_{class_index}"
        used.add(col)
        out[col] = float(recall)


def _flatten_screening_binary(
    metrics: Dict[str, Any],
    out: Dict[str, Any],
    *,
    key_prefix: str = "",
) -> None:
    screening = metrics.get("screening_binary")
    if not isinstance(screening, dict):
        return
    for src, dst in _SCREENING_SCALAR_MAP.items():
        val = screening.get(src)
        if isinstance(val, (int, float)):
            out[f"{key_prefix}{dst}"] = val


def _scalar_metrics_from_dict(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Extract scalar metrics from a validation_metrics dict (skip lists/nested)."""
    out: Dict[str, Any] = {}
    for k in SCALAR_KEYS:
        if k in metrics and isinstance(metrics[k], (int, float)):
            out[k] = metrics[k]
    _flatten_screening_binary(metrics, out)
    _flatten_per_class_recall(metrics, out)
    tr = metrics.get("training_metrics")
    ho = metrics.get("holdout_metrics")
    if isinstance(tr, dict):
        for k in SCALAR_KEYS:
            if k in tr and isinstance(tr[k], (int, float)):
                out[f"training_{k}"] = tr[k]
        _flatten_screening_binary(tr, out, key_prefix="training_")
        _flatten_per_class_recall(tr, out, key_prefix="training_")
    if isinstance(ho, dict):
        for k in SCALAR_KEYS:
            if k in ho and isinstance(ho[k], (int, float)):
                out[f"holdout_{k}"] = ho[k]
        _flatten_screening_binary(ho, out, key_prefix="holdout_")
        _flatten_per_class_recall(ho, out, key_prefix="holdout_")
    sem = metrics.get("evaluation_semantics")
    if isinstance(sem, str) and sem:
        out["evaluation_semantics"] = sem
    vm_schema = metrics.get("metrics_schema_version")
    if isinstance(vm_schema, str) and vm_schema:
        out["validation_metrics_schema_version"] = vm_schema
    return out


def load_metrics_from_json(path: str | Path) -> Dict[str, Any]:
    """Load a single validation_metrics.json file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_metrics_table(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Build a DataFrame from a list of row dicts (each has iteration, run_dir, and scalar metrics)."""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def write_all_metrics_csv(df: pd.DataFrame, path: str | Path) -> None:
    """Write the raw metrics table to CSV (one row per iteration)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def build_step_timings_table(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Build a DataFrame from step timing rows (step_name, duration_seconds, return_code, optional run_id, run_dir, n_train_samples, n_val_samples, n_processed_samples)."""
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def write_step_timings_csv(df_or_rows: pd.DataFrame | List[Dict[str, Any]], path: str | Path) -> None:
    """Write step timings to CSV. Accepts a DataFrame or a list of row dicts."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if isinstance(df_or_rows, pd.DataFrame):
        df_or_rows.to_csv(path, index=False)
    else:
        pd.DataFrame(df_or_rows).to_csv(path, index=False)


def compute_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Compute per-metric empirical distribution: mean, std, min, max, and percentiles (5, 25, 50, 75, 95).
    Only numeric columns are summarized.
    """
    summary: Dict[str, Any] = {
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
    }
    percentiles = [5, 25, 50, 75, 95]
    per_metric: Dict[str, Any] = {}
    for col in df.select_dtypes(include=[np.number]).columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue
        per_metric[col] = {
            "mean": float(series.mean()),
            "std": float(series.std()) if len(series) > 1 else 0.0,
            "min": float(series.min()),
            "max": float(series.max()),
            "count": int(len(series)),
        }
        try:
            p = np.percentile(series, percentiles)
            per_metric[col]["percentiles"] = {f"p{pct}": float(v) for pct, v in zip(percentiles, p)}
        except Exception:
            pass
    summary["metrics"] = per_metric
    robustness: Dict[str, Any] = {}
    ba_candidates = [
        c for c in ("holdout_balanced_accuracy", "balanced_accuracy", "training_balanced_accuracy") if c in df.columns
    ]
    if ba_candidates:
        key = ba_candidates[0]
        series = pd.to_numeric(df[key], errors="coerce").dropna()
        if len(series) > 0:
            robustness["balanced_accuracy_key"] = key
            robustness["balanced_accuracy_mean"] = float(series.mean())
            robustness["balanced_accuracy_variance"] = float(series.var()) if len(series) > 1 else 0.0
            robustness["balanced_accuracy_std"] = float(series.std()) if len(series) > 1 else 0.0
            robustness["balanced_accuracy_worst_run"] = float(series.min())
    if "ece" in df.columns:
        ece = pd.to_numeric(df["ece"], errors="coerce").dropna()
        if len(ece) > 0:
            robustness["calibration_ece_mean"] = float(ece.mean())
            robustness["calibration_ece_worst_run"] = float(ece.max())
    if robustness:
        summary["robustness"] = robustness
    # Backward compatibility: keep legacy top-level per-metric keys.
    summary.update(per_metric)
    return summary


def write_summary_json(summary: Dict[str, Any], path: str | Path) -> None:
    """Write the summary dict to JSON."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


def compute_resource_summary(timings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute resource summary from step timings: mean/std duration per step, mean total
    duration per iteration, and min/max/mean of n_train_samples, n_val_samples, and
    n_processed_samples (when present in rows).
    """
    if not timings:
        return {}
    # Per-step duration
    by_step: Dict[str, List[float]] = {}
    for row in timings:
        step = row.get("step_name")
        dur = row.get("duration_seconds")
        if step is not None and isinstance(dur, (int, float)):
            by_step.setdefault(step, []).append(float(dur))
    steps_summary: Dict[str, Any] = {}
    for step, durs in by_step.items():
        arr = np.array(durs)
        steps_summary[step] = {
            "mean_seconds": float(np.mean(arr)),
            "std_seconds": float(np.std(arr)) if len(arr) > 1 else 0.0,
            "count": int(len(arr)),
        }
    # Total duration per iteration (sum of steps per run_id)
    by_run: Dict[str, float] = {}
    for row in timings:
        run_id = row.get("run_id")
        dur = row.get("duration_seconds")
        if run_id is not None and isinstance(dur, (int, float)):
            by_run[run_id] = by_run.get(run_id, 0.0) + float(dur)
    total_durs = list(by_run.values()) if by_run else []
    iteration_summary: Dict[str, Any] = {}
    if total_durs:
        arr = np.array(total_durs)
        iteration_summary = {
            "mean_total_seconds": float(np.mean(arr)),
            "std_total_seconds": float(np.std(arr)) if len(arr) > 1 else 0.0,
            "n_iterations": int(len(arr)),
        }
    # n_train_samples, n_val_samples (one value per run)
    run_to_train: Dict[str, int] = {}
    run_to_val: Dict[str, int] = {}
    for row in timings:
        run_id = row.get("run_id")
        if run_id is None:
            continue
        if "n_train_samples" in row and row["n_train_samples"] is not None:
            run_to_train[run_id] = int(row["n_train_samples"])
        if "n_val_samples" in row and row["n_val_samples"] is not None:
            run_to_val[run_id] = int(row["n_val_samples"])
    sample_summary: Dict[str, Any] = {}
    if run_to_train:
        vals = list(run_to_train.values())
        sample_summary["n_train_samples"] = {
            "min": int(min(vals)),
            "max": int(max(vals)),
            "mean": float(np.mean(vals)),
        }
    if run_to_val:
        vals = list(run_to_val.values())
        sample_summary["n_val_samples"] = {
            "min": int(min(vals)),
            "max": int(max(vals)),
            "mean": float(np.mean(vals)),
        }
    processed_vals: List[int] = []
    for row in timings:
        val = row.get("n_processed_samples")
        if val is not None:
            try:
                processed_vals.append(int(val))
            except (TypeError, ValueError):
                continue
    if processed_vals:
        sample_summary["n_processed_samples"] = {
            "min": int(min(processed_vals)),
            "max": int(max(processed_vals)),
            "mean": float(np.mean(processed_vals)),
        }
    return {
        "per_step_duration_seconds": steps_summary,
        "per_iteration_total_seconds": iteration_summary,
        "sample_sizes": sample_summary,
    }


def write_resource_summary_json(summary: Dict[str, Any], path: str | Path) -> None:
    """Write the resource summary dict to JSON."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


def write_metrics_distribution_plotly(df: pd.DataFrame, path: str | Path) -> None:
    """
    Export Plotly HTML with KDE and ECDF for all numeric metrics.

    Produces one row per metric and two columns: KDE (left), ECDF (right).
    """
    numeric_cols = [c for c in df.select_dtypes(include=[np.number]).columns if c not in {"iteration"}]
    if not numeric_cols:
        raise ValueError("No numeric metrics available to plot.")
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception as e:
        raise RuntimeError(f"Plotly is required for chart export: {e}") from e
    try:
        from scipy.stats import gaussian_kde
    except Exception as e:
        raise RuntimeError(f"SciPy is required for KDE export: {e}") from e

    # Plotly enforces vertical_spacing <= 1 / (rows - 1). When many metrics are
    # present, use a reduced spacing to avoid runtime failures.
    if len(numeric_cols) > 1:
        max_vertical_spacing = 1.0 / float(len(numeric_cols) - 1)
        vertical_spacing = min(0.08, max(0.001, max_vertical_spacing - 1e-6))
    else:
        vertical_spacing = 0.0

    fig = make_subplots(
        rows=len(numeric_cols),
        cols=2,
        subplot_titles=[
            f"{numeric_cols[i // 2]} KDE" if i % 2 == 0 else f"{numeric_cols[i // 2]} ECDF"
            for i in range(len(numeric_cols) * 2)
        ],
        horizontal_spacing=0.08,
        vertical_spacing=vertical_spacing,
    )
    for row_idx, metric in enumerate(numeric_cols, start=1):
        series = pd.to_numeric(df[metric], errors="coerce").dropna().astype(float).values
        if len(series) == 0:
            continue
        xmin, xmax = float(np.min(series)), float(np.max(series))
        if xmin == xmax:
            x_grid = np.array([xmin - 1e-9, xmin, xmin + 1e-9], dtype=float)
            y_kde = np.array([0.0, 1.0, 0.0], dtype=float)
        else:
            x_grid = np.linspace(xmin, xmax, 256)
            try:
                kde = gaussian_kde(series)
                y_kde = kde(x_grid)
            except Exception:
                y_kde = np.zeros_like(x_grid)
        fig.add_trace(
            go.Scatter(x=x_grid, y=y_kde, mode="lines", name=f"{metric} KDE", showlegend=False),
            row=row_idx,
            col=1,
        )

        x_sorted = np.sort(series)
        y_ecdf = np.arange(1, len(x_sorted) + 1, dtype=float) / float(len(x_sorted))
        fig.add_trace(
            go.Scatter(x=x_sorted, y=y_ecdf, mode="lines", name=f"{metric} ECDF", showlegend=False),
            row=row_idx,
            col=2,
        )
        fig.update_yaxes(title_text="Density", row=row_idx, col=1)
        fig.update_yaxes(title_text="Cumulative Probability", row=row_idx, col=2, range=[0, 1])
        fig.update_xaxes(title_text=metric, row=row_idx, col=1)
        fig.update_xaxes(title_text=metric, row=row_idx, col=2)

    fig.update_layout(
        title_text="Monte Carlo Metric Distributions (KDE and ECDF)",
        height=max(360, 260 * len(numeric_cols)),
        width=1200,
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(path), include_plotlyjs="cdn", full_html=True)


def _find_validation_metrics_json_under_run(run_dir: Path) -> Optional[Path]:
    predictors = run_dir / "predictors"
    if predictors.is_dir():
        for path in predictors.rglob("test_metrics.json"):
            return path
        for path in predictors.rglob("validation_metrics.json"):
            return path
    for p in run_dir.glob("**/predictors"):
        if p.is_dir():
            for path in p.rglob("test_metrics.json"):
                return path
            for path in p.rglob("validation_metrics.json"):
                return path
    return None


def _detector_mean_balanced_accuracy(run_dir: Path) -> Optional[float]:
    values: List[float] = []
    for det_root in run_dir.rglob("detections"):
        if not det_root.is_dir():
            continue
        for p in det_root.rglob("result*.json"):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            ba = d.get("balanced_accuracy")
            if ba is not None and isinstance(ba, (int, float)):
                fv = float(ba)
                if fv == fv:
                    values.append(fv)
    return sum(values) / len(values) if values else None


def _featurecuts_mean_balanced_accuracy(run_dir: Path) -> Optional[float]:
    """Mean classifier-panel balanced accuracy from detector FeatureCuts exports.

    In ``dmp_modeling_mode=featurecuts`` runs the detector validates the selected
    classifier panel and writes the result to ``dmp-export-*.meta.json`` under
    ``featurecuts_validation_summary.balanced_accuracy`` (with
    ``classifier_panel_audit.balanced_accuracy_at_k_target`` as a fallback), rather
    than to a predictor ``validation_metrics.json`` or to the detector ``result*.json``
    top level. Aggregate those per-chromosome values so stability gating can score
    discovery-plus-FeatureCuts iterations.
    """
    values: List[float] = []
    for det_root in run_dir.rglob("detections"):
        if not det_root.is_dir():
            continue
        for p in det_root.rglob("dmp-export-*.meta.json"):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            ba: Any = None
            fc = d.get("featurecuts_validation_summary")
            if isinstance(fc, dict):
                ba = fc.get("balanced_accuracy")
            if not isinstance(ba, (int, float)):
                audit = d.get("classifier_panel_audit")
                if isinstance(audit, dict):
                    ba = audit.get("balanced_accuracy_at_k_target")
            if isinstance(ba, (int, float)):
                fv = float(ba)
                if fv == fv:
                    values.append(fv)
    return sum(values) / len(values) if values else None


def iteration_scalar_metrics_from_run_dir(run_dir: Path) -> Dict[str, Any]:
    """
    Scalar metrics for one Monte Carlo run directory.

    Prefer ``predictors/**/validation_metrics.json`` (after ``--predictor-only`` or ``--model``).
    Otherwise use the mean of ``balanced_accuracy`` from MethylDetector ``result*.json``
    files under ``detections/`` (centroid+detector iterations), and finally the
    FeatureCuts classifier-panel balanced accuracy from ``dmp-export-*.meta.json``
    (discovery-plus-FeatureCuts profiles without a predictor stage).
    """
    run_dir = Path(run_dir)
    vm = _find_validation_metrics_json_under_run(run_dir)
    if vm is not None:
        try:
            out = dict(_scalar_metrics_from_dict(load_metrics_from_json(vm)))
            if out:
                out["metrics_source"] = (
                    "model_test" if vm.name == "test_metrics.json" else "predictor"
                )
            return out
        except Exception:
            pass
    ba = _detector_mean_balanced_accuracy(run_dir)
    if ba is not None:
        return {"balanced_accuracy": ba, "metrics_source": "detector"}
    ba = _featurecuts_mean_balanced_accuracy(run_dir)
    if ba is not None:
        return {"balanced_accuracy": ba, "metrics_source": "detector_featurecuts"}
    return {}
