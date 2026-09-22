"""
Simple raw-gene ECDF OvR backend for methyl-validation --model.
"""

from __future__ import annotations

import json
import pickle
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from methyl_utils import load_project
from methyl_utils.ecdf_aggregated_ovr import (
    GENE_ECDF_OVR_TYPE,
    predict_aggregated_ecdf_ovr_proba,
    train_aggregated_ecdf_ovr_package,
)

from .classification_metrics import (
    compute_validation_metrics,
    resolve_class_roles,
)
from .ecdf_aggregated_backend import _build_predictor_eval_paths, _load_training_samples
from .model_bundle import load_bundle_dmp_index, load_bundle_frozen_gene_panel
from .raw_gene_features import (
    build_gene_panel_feature_weights,
    build_raw_gene_feature_table,
)


def train_ecdf_gene_ovr_model(
    *,
    project_json: str | Path,
    bundle_h5: str | Path,
    output_dir: str | Path,
    min_coverage: int = 1,
    use_region_weight: bool = True,
    gene_weight_column: str = "gene_importance",
    n_bins: int = 100,
    temperature: float = 2.0,
) -> Path:
    all_paths, y, class_names = _load_training_samples(project_json)
    dmp_df = load_bundle_dmp_index(bundle_h5)
    frozen_gene_panel_df = load_bundle_frozen_gene_panel(bundle_h5, project_json=project_json)
    if frozen_gene_panel_df.empty:
        raise ValueError(
            "raw_gene ECDF requires frozen_genes_production.csv from freeze. "
            "Run --freeze with mapper outputs or set step_config.model_bundle.fixed_gene_panel."
        )

    feat = build_raw_gene_feature_table(
        all_paths,
        dmp_df,
        frozen_gene_panel_df,
        min_coverage=int(max(1, min_coverage)),
        use_region_weight=bool(use_region_weight),
        gene_weight_column=str(gene_weight_column),
    )
    if feat.X.shape[1] == 0:
        raise ValueError("raw_gene ECDF requires at least one stable gene feature")

    feature_names = list(feat.feature_names)
    feature_weights = build_gene_panel_feature_weights(
        frozen_gene_panel_df,
        feature_names,
        weight_column=str(gene_weight_column),
    )
    roles = resolve_class_roles(load_project(project_json))
    package = train_aggregated_ecdf_ovr_package(
        np.asarray(feat.X, dtype=np.float64),
        y,
        class_names=class_names,
        feature_names=feature_names,
        feature_weights=feature_weights,
        feature_family_set="gene_scored",
        feature_mode="raw_gene",
        n_bins=int(max(8, n_bins)),
        temperature=float(temperature),
        package_metadata={
            "backend": "ecdf",
            "model_backend": "ecdf",
            "training_project_json": str(Path(project_json).resolve()),
            "package_notes": "Simple stable-gene ECDF OvR",
        },
        classifier_type=GENE_ECDF_OVR_TYPE,
    )
    package["feature_report"] = dict(feat.report)
    package["class_roles"] = roles
    package["raw_gene"] = {
        "dmp_df": dmp_df,
        "frozen_gene_panel": frozen_gene_panel_df,
        "min_coverage": int(max(1, min_coverage)),
        "use_region_weight": bool(use_region_weight),
        "gene_weight_column": str(gene_weight_column),
        "feature_names": feature_names,
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "ecdf_gene_ovr.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(package, f, protocol=pickle.HIGHEST_PROTOCOL)
    with open(out_dir / "ecdf_gene_ovr.meta.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "classifier_type": GENE_ECDF_OVR_TYPE,
                "class_names": [str(x) for x in class_names],
                "n_features": int(len(feature_names)),
                "feature_names": feature_names,
                "feature_mode": "raw_gene",
                "feature_family_set": "gene_scored",
            },
            f,
            indent=2,
        )
    return model_path


def predict_ecdf_gene_ovr_from_project(
    *,
    project_json: str | Path,
    model_path: str | Path,
    output_dir: str | Path,
    evaluation_partition: Optional[str] = None,
) -> Dict[str, Any]:
    with open(model_path, "rb") as f:
        package = pickle.load(f)
    if not isinstance(package, dict) or package.get("classifier_type") != GENE_ECDF_OVR_TYPE:
        raise ValueError(f"Model file is not {GENE_ECDF_OVR_TYPE}: {model_path}")

    class_names = [str(x) for x in (package.get("class_names") or [])]
    if len(class_names) < 2:
        raise ValueError("Gene ECDF package missing class names")
    raw = package.get("raw_gene") or {}
    dmp_df = raw.get("dmp_df")
    frozen_gene_panel = raw.get("frozen_gene_panel")
    if dmp_df is None or not hasattr(dmp_df, "columns"):
        raise ValueError("Gene ECDF package missing raw_gene.dmp_df")
    if frozen_gene_panel is None or not hasattr(frozen_gene_panel, "columns"):
        raise ValueError("Gene ECDF package missing raw_gene.frozen_gene_panel")

    partition = str(evaluation_partition or "").strip().lower()
    eval_paths, eval_y = _build_predictor_eval_paths(
        project_json,
        class_names,
        evaluation_partition=partition or None,
    )
    if not eval_paths:
        raise ValueError("No evaluation paths resolved for gene ECDF predictor")

    feat = build_raw_gene_feature_table(
        eval_paths,
        dmp_df,
        frozen_gene_panel,
        min_coverage=int(raw.get("min_coverage", 1)),
        use_region_weight=bool(raw.get("use_region_weight", True)),
        gene_weight_column=str(raw.get("gene_weight_column") or "gene_importance"),
    )
    schema_names = [str(x) for x in ((package.get("feature_schema") or {}).get("feature_names") or [])]
    if list(feat.feature_names) != schema_names:
        raise ValueError("Gene ECDF feature schema mismatch between training and prediction")

    probs, evidence = predict_aggregated_ecdf_ovr_proba(
        package,
        np.asarray(feat.X, dtype=np.float64),
    )
    pred = np.argmax(probs, axis=1).astype(int)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pred_name = f"{partition}_predictions.csv" if partition in {"train", "test"} else "predictions.csv"
    pred_csv = out_dir / pred_name
    df = pd.DataFrame(
        {
            "sample": [str(p) for p in eval_paths],
            "prediction": pred.astype(int),
            "prediction_label": [class_names[int(i)] for i in pred.tolist()],
        }
    )
    for j in range(probs.shape[1]):
        df[f"prob_class{j}"] = probs[:, j]
        df[f"evidence_class{j}"] = evidence[:, j]
    if eval_y is not None and len(eval_y) == len(df):
        df["expected_class"] = eval_y.astype(int)
    df.to_csv(pred_csv, index=False)

    metrics: Dict[str, Any] = {
        "classifier_type": GENE_ECDF_OVR_TYPE,
        "n_samples": int(len(df)),
        "n_classes": int(len(class_names)),
        "class_names": [str(x) for x in class_names],
        "evaluation_partition": partition or "unspecified",
        "probability_semantics": {
            "posterior_source": "gene_ecdf_ovr_softmax",
            "evidence_columns": [f"evidence_class{i}" for i in range(len(class_names))],
            "note": "evidence_class* are pre-softmax OvR log-evidence diagnostics, not p-values.",
        },
    }
    if partition in {"train", "test"}:
        train_count = sum(
            len(paths or []) for _label, paths in load_project(project_json).get_resolved_groups()
        )
        metrics["n_train_samples"] = int(train_count)
        metrics["n_test_samples"] = int(len(df)) if partition == "test" else None
        metrics["train_test_overlap_count"] = 0 if partition == "test" else None
    if "expected_class" in df.columns:
        y_true = df["expected_class"].to_numpy(dtype=int)
        y_pred = df["prediction"].to_numpy(dtype=int)
        class_roles = package.get("class_roles")
        if not isinstance(class_roles, dict):
            class_roles = resolve_class_roles(load_project(project_json))
        scored = compute_validation_metrics(
            y_true, y_pred, class_names, class_roles=class_roles
        )
        metrics.update(scored)

    metrics_name = f"{partition}_metrics.json" if partition in {"train", "test"} else "validation_metrics.json"
    metrics_path = out_dir / metrics_name
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    if partition == "test":
        shutil.copy2(pred_csv, out_dir / "predictions.csv")
        shutil.copy2(metrics_path, out_dir / "validation_metrics.json")
    report_path = out_dir / "prediction_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "model_type": GENE_ECDF_OVR_TYPE,
                "predictions_csv": str(pred_csv),
                "validation_metrics": str(metrics_path),
                "n_samples": int(len(df)),
                "n_classes": int(len(class_names)),
            },
            f,
            indent=2,
        )
    return metrics
