"""
Aggregated-feature ECDF OvR backend for methyl-validation --model.
"""

from __future__ import annotations

import json
import pickle
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from methyl_predictor.models.config import PredictorConfig
from methyl_predictor.project_resolver import resolve_predictor_config
from methyl_utils import load_project
from methyl_utils.ecdf_aggregated_ovr import (
    AGGREGATED_ECDF_OVR_TYPE,
    build_effect_size_feature_weights,
    predict_aggregated_ecdf_ovr_proba,
    train_aggregated_ecdf_ovr_package,
)

from .classification_metrics import (
    compute_validation_metrics,
    resolve_class_roles,
    select_healthy_index_for_labels,
)
from .eval_split_resolver import resolve_eval_paths_and_labels
from .model_bundle import load_bundle_dmp_index
from .observed_feature_builder import (
    build_observed_hybrid_feature_table,
    coerce_saved_feature_family_set,
    derive_observed_hybrid_anchors,
    normalize_feature_family_set,
    observed_chromosome_build_kwargs,
    select_training_feature_matrix,
)
from .tabular_backend import _resolve_class_centroid_dirs


def _load_training_samples(project_json: str | Path) -> Tuple[List[str], np.ndarray, List[str]]:
    from .eval_split_resolver import assert_model_mc_train_partition

    project = load_project(project_json)
    resolved = project.get_resolved_groups()
    if len(resolved) < 2:
        raise ValueError("Aggregated ECDF requires at least two resolved training groups")
    all_paths: List[str] = []
    y: List[int] = []
    class_names: List[str] = []
    for cls, (label, paths) in enumerate(resolved):
        class_names.append(str(label))
        for p in paths:
            all_paths.append(str(p))
            y.append(int(cls))
    if not all_paths:
        raise ValueError("No training samples resolved from project")
    assert_model_mc_train_partition(project_json, train_sample_paths=all_paths)
    return all_paths, np.asarray(y, dtype=np.int32), class_names


def train_ecdf_aggregated_ovr_model(
    *,
    project_json: str | Path,
    bundle_h5: str | Path,
    output_dir: str | Path,
    feature_family_set: str = "gene_scored",
    observed_feature_min_coverage: int = 1,
    observed_feature_quality_columns: Optional[Sequence[str]] = None,
    observed_hist_eps: float = 1e-6,
    observed_hist_alpha: float = 0.5,
    observed_hist_evidence_clip_cap: float = 5.0,
    observed_hist_tail_agreement_threshold: float = 0.10,
    chromosome_hypo_beta_threshold: Optional[float] = None,
    chromosome_intermediate_beta_lo: Optional[float] = None,
    chromosome_intermediate_beta_hi: Optional[float] = None,
    chromosome_distance_metrics: Optional[List[str]] = None,
    chromosome_list: Optional[List[str]] = None,
    n_bins: int = 100,
    temperature: float = 2.0,
) -> Path:
    feature_family_set = normalize_feature_family_set(feature_family_set)
    project = load_project(project_json)
    all_paths, y, class_names = _load_training_samples(project_json)
    roles = resolve_class_roles(project)
    dmp_df = load_bundle_dmp_index(bundle_h5)
    class_centroid_dirs = _resolve_class_centroid_dirs(project, class_names)

    anchors = derive_observed_hybrid_anchors(
        all_paths,
        y.tolist(),
        class_names,
        dmp_df,
        min_coverage=int(max(1, observed_feature_min_coverage)),
    )
    feat = build_observed_hybrid_feature_table(
        all_paths,
        dmp_df,
        min_coverage=int(max(1, observed_feature_min_coverage)),
        healthy_reference_vector=anchors.healthy_reference_vector.tolist(),
        cancer_reference_vector=anchors.cancer_reference_vector.tolist(),
        per_cancer_reference_vectors=[v.tolist() for v in anchors.per_cancer_reference_vectors],
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        all_class_labels=class_names,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        centroid_dir_by_class_label=class_centroid_dirs,
        hist_eps=float(observed_hist_eps),
        hist_alpha=float(observed_hist_alpha),
        hist_evidence_clip_cap=float(observed_hist_evidence_clip_cap),
        hist_tail_agreement_threshold=float(observed_hist_tail_agreement_threshold),
        feature_family_set=str(feature_family_set),
        observed_feature_quality_columns=observed_feature_quality_columns,
        **observed_chromosome_build_kwargs(
            chromosome_hypo_beta_threshold=chromosome_hypo_beta_threshold,
            chromosome_intermediate_beta_lo=chromosome_intermediate_beta_lo,
            chromosome_intermediate_beta_hi=chromosome_intermediate_beta_hi,
            chromosome_distance_metrics=chromosome_distance_metrics,
            chromosome_list=chromosome_list,
        ),
    )
    export_feature_names = list(feat.feature_names)
    training_feature_names = list(feat.training_feature_names)
    quality_feature_names = list(feat.quality_feature_names)
    X_train = select_training_feature_matrix(
        np.asarray(feat.X, dtype=np.float64),
        export_feature_names,
        training_feature_names,
    )
    feature_weights = build_effect_size_feature_weights(dmp_df, training_feature_names)
    package = train_aggregated_ecdf_ovr_package(
        X_train,
        y,
        class_names=class_names,
        feature_names=training_feature_names,
        feature_weights=feature_weights,
        feature_family_set=str(feature_family_set),
        feature_mode="observed_hybrid",
        n_bins=int(max(8, n_bins)),
        temperature=float(temperature),
        package_metadata={
            "backend": "ecdf",
            "model_backend": "ecdf",
            "training_project_json": str(Path(project_json).resolve()),
            "package_notes": "Aggregated observed-hybrid ECDF OvR",
        },
    )
    package["feature_report"] = dict(feat.report)
    package["class_roles"] = roles
    package["observed_hybrid"] = {
        "dmp_df": dmp_df,
        "healthy_reference_vector": anchors.healthy_reference_vector.astype(np.float64),
        "cancer_reference_vector": anchors.cancer_reference_vector.astype(np.float64),
        "per_cancer_reference_vectors": [
            np.asarray(v, dtype=np.float64) for v in anchors.per_cancer_reference_vectors
        ],
        "healthy_class_label": str(anchors.healthy_class_label),
        "cancer_class_labels": [str(x) for x in anchors.cancer_class_labels],
        "anchor_strategy": str(anchors.anchor_strategy),
        "feature_order_fingerprint": str(anchors.feature_order_fingerprint),
        "feature_family_set": str(feature_family_set),
        "min_coverage": int(max(1, observed_feature_min_coverage)),
        "hist_eps": float(observed_hist_eps),
        "hist_alpha": float(observed_hist_alpha),
        "hist_evidence_clip_cap": float(observed_hist_evidence_clip_cap),
        "hist_tail_agreement_threshold": float(observed_hist_tail_agreement_threshold),
        "class_centroid_dirs": {str(k): str(v) for k, v in class_centroid_dirs.items()},
        "export_feature_names": export_feature_names,
        "training_feature_names": training_feature_names,
        "quality_feature_names": quality_feature_names,
        "observed_feature_quality_columns": [
            str(x) for x in (observed_feature_quality_columns or ["obs_fraction", "n_obs_dmps", "n_total_dmps"])
        ],
    }

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "ecdf_aggregated_ovr.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(package, f, protocol=pickle.HIGHEST_PROTOCOL)
    with open(out_dir / "ecdf_aggregated_ovr.meta.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "classifier_type": AGGREGATED_ECDF_OVR_TYPE,
                "class_names": [str(x) for x in class_names],
                "n_features": int(len(training_feature_names)),
                "n_export_features": int(len(export_feature_names)),
                "training_feature_names": training_feature_names,
                "quality_feature_names": quality_feature_names,
                "feature_family_set": str(feature_family_set),
                "raw_mapped_feature_formula": str(
                    feat.report.get("raw_mapped_feature_formula", "unknown")
                ),
                "raw_mapped_feature_counts": dict(
                    feat.report.get("raw_mapped_feature_counts") or {}
                ),
            },
            f,
            indent=2,
        )
    return model_path


def _build_predictor_eval_paths(
    project_json: str | Path,
    class_names: List[str],
    evaluation_partition: Optional[str] = None,
) -> Tuple[List[str], Optional[np.ndarray]]:
    predictor_cfg = resolve_predictor_config(project_json)
    eval_paths, eval_y = resolve_eval_paths_and_labels(
        project_json,
        class_names,
        predictor_cfg=predictor_cfg,
        project_loader=load_project,
        evaluation_partition=evaluation_partition,
    )
    if eval_y is None:
        return eval_paths, None
    return eval_paths, np.asarray(eval_y, dtype=np.int32)


def predict_ecdf_aggregated_ovr_from_project(
    *,
    project_json: str | Path,
    model_path: str | Path,
    output_dir: str | Path,
    evaluation_partition: Optional[str] = None,
) -> Dict[str, Any]:
    with open(model_path, "rb") as f:
        package = pickle.load(f)
    if not isinstance(package, dict) or package.get("classifier_type") != AGGREGATED_ECDF_OVR_TYPE:
        raise ValueError(f"Model file is not {AGGREGATED_ECDF_OVR_TYPE}: {model_path}")

    class_names = [str(x) for x in (package.get("class_names") or [])]
    if len(class_names) < 2:
        raise ValueError("Aggregated ECDF package missing class names")
    obs = package.get("observed_hybrid") or {}
    dmp_df = obs.get("dmp_df")
    if dmp_df is None or not hasattr(dmp_df, "columns"):
        raise ValueError("Aggregated ECDF package missing observed_hybrid.dmp_df")

    partition = str(evaluation_partition or "").strip().lower()
    eval_paths, eval_y = _build_predictor_eval_paths(
        project_json,
        class_names,
        evaluation_partition=partition or None,
    )
    if not eval_paths:
        raise ValueError("No evaluation paths resolved for aggregated ECDF predictor")

    feat = build_observed_hybrid_feature_table(
        eval_paths,
        dmp_df,
        min_coverage=int(obs.get("min_coverage", 1)),
        healthy_reference_vector=np.asarray(obs.get("healthy_reference_vector"), dtype=np.float64).tolist(),
        cancer_reference_vector=np.asarray(obs.get("cancer_reference_vector"), dtype=np.float64).tolist(),
        per_cancer_reference_vectors=[
            np.asarray(v, dtype=np.float64).tolist()
            for v in (obs.get("per_cancer_reference_vectors") or [])
        ],
        healthy_class_label=str(
            obs.get("healthy_class_label")
            or class_names[select_healthy_index_for_labels(class_names)]
        ),
        cancer_class_labels=[str(x) for x in (obs.get("cancer_class_labels") or class_names[1:])],
        all_class_labels=class_names,
        anchor_strategy=str(obs.get("anchor_strategy") or "class_centroid"),
        expected_feature_order_fingerprint=str(obs.get("feature_order_fingerprint") or ""),
        centroid_dir_by_class_label={str(k): str(v) for k, v in (obs.get("class_centroid_dirs") or {}).items()},
        hist_eps=float(obs.get("hist_eps", 1e-6)),
        hist_alpha=float(obs.get("hist_alpha", 0.5)),
        hist_evidence_clip_cap=float(obs.get("hist_evidence_clip_cap", 5.0)),
        hist_tail_agreement_threshold=float(obs.get("hist_tail_agreement_threshold", 0.10)),
        feature_family_set=coerce_saved_feature_family_set(str(obs.get("feature_family_set") or "gene_scored")),
        observed_feature_quality_columns=obs.get("observed_feature_quality_columns"),
    )
    export_names = [str(x) for x in (obs.get("export_feature_names") or feat.feature_names)]
    training_names = [str(x) for x in (obs.get("training_feature_names") or feat.training_feature_names)]
    if list(feat.feature_names) != export_names:
        raise ValueError("Aggregated ECDF export feature schema mismatch between training and prediction")
    schema_names = [str(x) for x in ((package.get("feature_schema") or {}).get("feature_names") or [])]
    if training_names and list(training_names) != schema_names:
        raise ValueError("Aggregated ECDF training feature schema mismatch between training and prediction")

    X_train = select_training_feature_matrix(
        np.asarray(feat.X, dtype=np.float64),
        export_names,
        training_names or schema_names,
    )
    probs, evidence = predict_aggregated_ecdf_ovr_proba(
        package,
        X_train,
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
        "classifier_type": AGGREGATED_ECDF_OVR_TYPE,
        "n_samples": int(len(df)),
        "n_classes": int(len(class_names)),
        "class_names": [str(x) for x in class_names],
        "evaluation_partition": partition or "unspecified",
        "probability_semantics": {
            "posterior_source": "aggregated_ecdf_ovr_softmax",
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
                "model_type": AGGREGATED_ECDF_OVR_TYPE,
                "predictions_csv": str(pred_csv),
                "validation_metrics": str(metrics_path),
                "n_samples": int(len(df)),
                "n_classes": int(len(class_names)),
            },
            f,
            indent=2,
        )
    return metrics
