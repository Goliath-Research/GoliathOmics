"""
Tabular sklearn backend for methyl-validation --model.

Builds features from bundle-selected DMP loci and optional covariates, then trains
and evaluates a multiclass classifier.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
try:
    from xgboost import XGBClassifier
except ImportError:  # pragma: no cover - handled at runtime when xgboost method is requested
    XGBClassifier = None

from methyl_predictor.project_resolver import resolve_predictor_config
from methyl_utils import load_project
from methyl_utils.methyl_centroid_pair import MethylCentroidPair

from .classification_metrics import (
    CLASSIFICATION_RESULTS_FILENAME,
    classifier_comparison_output_dir,
    compute_validation_metrics,
    resolve_class_roles,
    select_healthy_index_for_labels,
    write_classification_results_csv,
)
from .covariate_preprocessor import (
    CovariatePreprocessor,
    exclusion_report,
    fit_covariates,
    normalize_composition_groups,
    resolve_covariate_sample_ids,
    transform_covariates,
)
from .eval_split_resolver import assert_model_mc_train_partition, resolve_eval_paths_and_labels
from .gene_scored_features import DEFAULT_REGION_DIRECTIONAL_TYPES, family_includes_gene_scored
from .structural_scored_features import (
    family_includes_structural_scored,
    preflight_structural_scored_training,
)
from .model_datasets import (
    TRAIN_DATASET_NAME,
    TEST_DATASET_NAME,
    dataset_sidecar_meta_path,
    read_dataset_frame,
    write_dataset_frame,
    write_dataset_manifest,
)
from .model_bundle import (
    load_bundle_dmp_index,
    load_bundle_frozen_gene_panel,
    load_bundle_gene_feature_ranges,
    resolve_fixed_gene_features_panel,
)
from .observed_feature_builder import (
    OBSERVED_HYBRID_SCHEMA_VERSION,
    apply_feature_fill_values,
    build_observed_hybrid_feature_table,
    coerce_saved_feature_family_set,
    derive_observed_hybrid_anchors,
    describe_active_feature_families,
    fit_feature_fill_values,
    normalize_feature_family_set,
    observed_chromosome_build_kwargs,
    observed_hybrid_feature_names,
    observed_hybrid_schema_fingerprint,
    select_training_feature_matrix,
    sample_ids_from_paths,
    verify_feature_schema,
)


@contextmanager
def _project_cwd(project_json: str | Path):
    pj = Path(project_json).resolve()
    prev = Path.cwd()
    try:
        os.chdir(pj.parent)
        yield
    finally:
        os.chdir(prev)


def _dataset_meta_path(dataset_path: Path) -> Path:
    return dataset_sidecar_meta_path(dataset_path)


def _write_dataset_frame(dataset_path: Path, frame: pd.DataFrame) -> None:
    write_dataset_frame(dataset_path, frame)


def _read_dataset_frame(dataset_path: Path) -> pd.DataFrame:
    return read_dataset_frame(dataset_path)


def _dataset_to_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, List[str], List[str], List[str]]:
    required = {"sample_id", "class_index", "class_label"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Dataset is missing required columns: {missing}")
    feature_names = [str(c) for c in frame.columns.tolist() if c not in required]
    X = frame[feature_names].to_numpy(dtype=np.float32)
    y_arr = frame["class_index"].to_numpy(dtype=np.int32)
    sample_ids = [str(x) for x in frame["sample_id"].astype(str).tolist()]
    class_names = [str(x) for x in frame["class_label"].astype(str).tolist()]
    return X, y_arr, sample_ids, class_names, feature_names


def _require_non_empty_training_matrix(
    X: np.ndarray,
    *,
    feature_mode: str,
    feature_family_set: str,
) -> None:
    n_features = int(X.shape[1]) if getattr(X, "ndim", 0) == 2 else 0
    if n_features > 0:
        return
    raise ValueError(
        f"tabular training matrix has zero feature columns "
        f"(feature_mode={feature_mode}, feature_family_set={feature_family_set}). "
        "For feature_family_set=structural_scored, rebuild frozen_gene_features.csv and ensure "
        "structural column specs resolve; delete any cached train_dataset.h5 that "
        "was written with zero features."
    )


def _fingerprint_payload(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _dmp_index_fingerprint(dmp_df: pd.DataFrame) -> str:
    if dmp_df.empty:
        return _fingerprint_payload({"rows": []})
    cols = [c for c in ("chromosome", "context", "position", "effect_size") if c in dmp_df.columns]
    rows = dmp_df[cols].copy().sort_values(["chromosome", "context", "position"]).to_dict(orient="records")
    return _fingerprint_payload({"rows": rows})


def _derive_test_dataset_path(
    train_dataset_out_path: Optional[Path],
    explicit_test_dataset_path: Optional[str | Path],
    out_dir: Path,
    bundle_dir: Optional[Path],
) -> Path:
    if explicit_test_dataset_path:
        return Path(explicit_test_dataset_path).expanduser().resolve()
    if train_dataset_out_path is not None:
        ext = train_dataset_out_path.suffix or ".h5"
        return train_dataset_out_path.with_name(f"test_dataset{ext}")
    if bundle_dir is not None:
        return bundle_dir / TEST_DATASET_NAME
    return out_dir / TEST_DATASET_NAME


def _resolve_class_centroid_dirs(project: Any, class_names: Sequence[str]) -> Dict[str, str]:
    label_to_dir: Dict[str, str] = {}
    try:
        resolved = project.get_resolved_groups()
        derived = project.get_derived_paths()
        centroid_dirs = list(getattr(derived, "centroid_dirs", []) or [])
        for idx, (label, _paths) in enumerate(resolved):
            if idx < len(centroid_dirs):
                label_to_dir[str(label)] = str(centroid_dirs[idx])
    except Exception:
        label_to_dir = {}
    for name in class_names:
        label_to_dir.setdefault(str(name), "")
    return {k: v for k, v in label_to_dir.items() if str(v).strip()}


def _build_reference_map(
    dmp_df: pd.DataFrame,
) -> Tuple[Dict[str, Dict[str, np.ndarray]], List[Tuple[str, str, int]]]:
    refs: Dict[str, Dict[str, np.ndarray]] = {}
    order: List[Tuple[str, str, int]] = []
    for chrom, cdf in dmp_df.groupby("chromosome", sort=True):
        refs[str(chrom)] = {}
        for ctx, xdf in cdf.groupby("context", sort=False):
            poss = np.asarray(sorted(set(int(v) for v in xdf["position"].tolist())), dtype=np.uint32)
            refs[str(chrom)][str(ctx)] = poss
        for _, row in cdf.iterrows():
            order.append((str(row["chromosome"]), str(row["context"]), int(row["position"])))
    return refs, order


def _extract_matrix_for_samples(
    sample_paths: Sequence[str],
    refs: Dict[str, Dict[str, np.ndarray]],
    feature_order: List[Tuple[str, str, int]],
    min_coverage: int = 1,
) -> np.ndarray:
    n_samples = len(sample_paths)
    if n_samples == 0:
        return np.zeros((0, len(feature_order)), dtype=np.float32)

    blocks: List[np.ndarray] = []
    for chrom in sorted(refs.keys(), key=lambda x: (len(str(x)), str(x))):
        X, all_positions, all_contexts, _idx = MethylCentroidPair.extract_methylation_fractions(
            list(sample_paths),
            refs[chrom],
            chromosome=chrom,
            min_coverage=min_coverage,
        )
        col_map: Dict[Tuple[str, str, int], int] = {}
        for j in range(len(all_positions)):
            col_map[(str(chrom), str(all_contexts[j]), int(all_positions[j]))] = int(j)
        wanted_cols = [col_map[(chrom, ctx, pos)] for (c, ctx, pos) in feature_order if c == chrom]
        if wanted_cols:
            blocks.append(X[:, wanted_cols])
    if not blocks:
        return np.full((n_samples, len(feature_order)), np.nan, dtype=np.float32)
    X_all = np.concatenate(blocks, axis=1)
    return X_all


def _normalize_tabular_methods(
    tabular_methods: Optional[Sequence[Any]],
    legacy_model_type: str,
) -> List[Dict[str, Any]]:
    if tabular_methods:
        out: List[Dict[str, Any]] = []
        for item in tabular_methods:
            if hasattr(item, "model_dump"):
                payload = item.model_dump(mode="python")
            elif isinstance(item, dict):
                payload = dict(item)
            else:
                raise ValueError(f"Unsupported tabular method config type: {type(item)!r}")
            method = str(payload.get("method") or "").strip().lower()
            if not method:
                raise ValueError("Each tabular method entry requires non-empty 'method'")
            params = payload.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError(f"tabular method params must be an object for method={method}")
            out.append({"method": method, "params": dict(params)})
        if out:
            return out
    return [{"method": str(legacy_model_type or "random_forest").strip().lower(), "params": {}}]


def _build_estimator_from_config(method_cfg: Dict[str, Any]):
    method = str(method_cfg.get("method") or "random_forest").strip().lower()
    params = dict(method_cfg.get("params") or {})
    if method == "hist_gradient_boosting":
        resolved = {
            "random_state": int(params.get("random_state", 13)),
            "learning_rate": float(params.get("learning_rate", 0.1)),
            "max_iter": int(params.get("max_iter", 100)),
            "max_depth": (
                int(params["max_depth"])
                if params.get("max_depth") is not None
                else None
            ),
        }
        return HistGradientBoostingClassifier(**resolved), resolved
    if method == "logistic_regression":
        resolved = {
            "max_iter": int(params.get("max_iter", 1000)),
            "class_weight": str(params.get("class_weight", "balanced")),
            "random_state": int(params.get("random_state", 13)),
            "C": float(params.get("c", params.get("C", 1.0))),
            "solver": str(params.get("solver", "lbfgs")),
            "penalty": str(params.get("penalty", "l2")),
        }
        return LogisticRegression(**resolved), resolved
    if method == "xgboost":
        if XGBClassifier is None:
            raise ImportError(
                "xgboost is required for tabular method 'xgboost'. "
                "Install xgboost in the runtime environment."
            )
        resolved = {
            "n_estimators": int(params.get("n_estimators", 300)),
            "max_depth": int(params.get("max_depth", 6)),
            "learning_rate": float(params.get("learning_rate", 0.1)),
            "subsample": float(params.get("subsample", 1.0)),
            "colsample_bytree": float(params.get("colsample_bytree", 1.0)),
            "min_child_weight": float(params.get("min_child_weight", 1.0)),
            "reg_lambda": float(params.get("reg_lambda", params.get("lambda", 1.0))),
            "random_state": int(params.get("random_state", 13)),
            "n_jobs": int(params.get("n_jobs", -1)),
            "tree_method": str(params.get("tree_method", "hist")),
            "eval_metric": str(params.get("eval_metric", "mlogloss")),
            "verbosity": int(params.get("verbosity", 0)),
        }
        objective = params.get("objective")
        if objective is not None:
            resolved["objective"] = str(objective)
        return XGBClassifier(**resolved), resolved
    if method != "random_forest":
        raise ValueError(f"Unsupported tabular method: {method}")
    resolved = {
        "n_estimators": int(params.get("n_estimators", 300)),
        "min_samples_leaf": int(params.get("min_samples_leaf", 2)),
        "n_jobs": int(params.get("n_jobs", -1)),
        "class_weight": str(params.get("class_weight", "balanced_subsample")),
        "random_state": int(params.get("random_state", 13)),
    }
    return RandomForestClassifier(**resolved), resolved


def _resolve_eval_paths_and_labels(
    project_json: str | Path,
    class_names: List[str],
    *,
    evaluation_partition: Optional[str] = None,
) -> Tuple[List[str], np.ndarray]:
    predictor_cfg = None
    partition = str(evaluation_partition or "").strip().lower()
    if partition not in {"train", "test"}:
        predictor_cfg = resolve_predictor_config(project_json)
    samples, y_true = resolve_eval_paths_and_labels(
        project_json,
        class_names,
        predictor_cfg=predictor_cfg,
        project_loader=load_project,
        evaluation_partition=partition or None,
    )
    if y_true is None:
        raise ValueError("No labeled evaluation samples resolved for tabular prediction.")
    if not samples:
        raise ValueError(
            f"No evaluation samples resolved for tabular prediction"
            f"{f' (partition={partition})' if partition else ''}."
        )
    return samples, y_true


def train_tabular_model(
    project_json: str | Path,
    bundle_h5: str | Path,
    output_dir: str | Path,
    *,
    bundle_dir: Optional[str | Path] = None,
    model_type: str = "random_forest",
    tabular_methods: Optional[Sequence[Any]] = None,
    tabular_method_selection_metric: str = "balanced_accuracy",
    tabular_method_selection_stat: str = "mean",
    max_dmps: Optional[int] = None,
    covariates_path: Optional[str] = None,
    covariate_id_column: str = "sample_id",
    covariates_strict_join: bool = False,
    covariates_missing_samples: Optional[str] = None,
    covariate_numeric_columns: Optional[List[str]] = None,
    covariate_ordinal_columns: Optional[List[str]] = None,
    covariate_ordinal_maps: Optional[Dict[str, Dict[str, float]]] = None,
    covariate_ordinal_unknown_value: float = 0.0,
    covariate_categorical_columns: Optional[List[str]] = None,
    covariate_missing_numeric_strategy: str = "mean",
    covariate_standardize_numeric: bool = True,
    covariate_composition_groups: Optional[Sequence[Any]] = None,
    covariate_composition_transform: Optional[str] = None,
    covariate_composition_columns: Optional[List[str]] = None,
    covariate_composition_reference: Optional[str] = None,
    covariate_composition_pseudocount: Optional[float] = None,
    feature_mode: str = "raw_dmp",
    observed_feature_quantiles: Optional[List[float]] = None,
    observed_feature_min_coverage: int = 1,
    observed_feature_min_obs_fraction: float = 0.0,
    observed_feature_include_dmp: bool = True,
    observed_feature_include_chromosome: bool = True,
    observed_feature_include_dmr: bool = True,
    observed_feature_include_gene: bool = True,
    observed_feature_dmr_window_bp: int = 100000,
    observed_feature_max_dmrs: int = 32,
    observed_feature_max_genes: int = 32,
    feature_family_set: str = "dmp_scored",
    gene_feature_loading: str = "frozen",
    observed_hist_eps: float = 1e-6,
    observed_hist_alpha: float = 0.5,
    observed_hist_evidence_clip_cap: float = 5.0,
    observed_hist_tail_agreement_threshold: float = 0.10,
    gene_scored_min_support_n: int = 2,
    gene_scored_use_region_weight: bool = True,
    gene_scored_gene_weight: str = "importance_x_sqrt_support",
    gene_scored_ordered_comparison_labels: Optional[List[str]] = None,
    gene_scored_contrast_pairs: Optional[List[List[str]]] = None,
    structural_scored_min_support_n: int = 2,
    structural_scored_use_region_weight: bool = True,
    structural_scored_weight: str = "compound_x_sqrt_support",
    structural_scored_ordered_comparison_labels: Optional[List[str]] = None,
    structural_scored_contrast_pairs: Optional[List[List[str]]] = None,
    region_directional_region_types: Optional[List[str]] = None,
    region_directional_min_loci: int = 1,
    observed_feature_quality_columns: Optional[List[str]] = None,
    chromosome_hypo_beta_threshold: Optional[float] = None,
    chromosome_intermediate_beta_lo: Optional[float] = None,
    chromosome_intermediate_beta_hi: Optional[float] = None,
    chromosome_distance_metrics: Optional[List[str]] = None,
    chromosome_list: Optional[List[str]] = None,
    save_train_dataset: bool = False,
    reuse_train_dataset: bool = True,
    train_dataset_path: Optional[str | Path] = None,
    save_test_dataset: bool = True,
    test_dataset_path: Optional[str | Path] = None,
) -> Path:
    with _project_cwd(project_json):
        project = load_project(project_json)
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    dmp_df = load_bundle_dmp_index(bundle_h5)
    bundle_h5_path = Path(bundle_h5).expanduser().resolve()
    bundle_dir_for_panel = bundle_h5_path.parent
    if bundle_dir is not None:
        bundle_dir_for_panel = Path(bundle_dir).expanduser().resolve()
    if family_includes_structural_scored(feature_family_set):
        fixed_gene_features_df, _fixed_gene_features_path = resolve_fixed_gene_features_panel(
            project_json=project_json,
            bundle_dir=bundle_dir_for_panel,
            project=project,
            bundle_h5=bundle_h5_path,
            auto_rebuild=True,
        )
    else:
        fixed_gene_features_df = load_bundle_gene_feature_ranges(bundle_h5_path)
    frozen_gene_panel_df = pd.DataFrame()
    if family_includes_gene_scored(feature_family_set):
        frozen_gene_panel_df = load_bundle_frozen_gene_panel(
            bundle_h5,
            project_json=project_json,
        )
        if frozen_gene_panel_df.empty:
            raise FileNotFoundError(
                "feature_family_set includes gene_scored but frozen_genes_production.csv was not found. "
                "Run freeze with build_frozen_gene_panel or set step_config.model_bundle.fixed_gene_panel."
            )
    if family_includes_structural_scored(feature_family_set):
        if fixed_gene_features_df.empty:
            raise FileNotFoundError(
                "feature_family_set includes structural_scored but frozen_gene_features.csv was not found. "
                "Run freeze with build_frozen_gene_panel or set step_config.model_bundle.fixed_gene_features."
            )
    max_dmps_norm = int(max_dmps) if (max_dmps is not None and int(max_dmps) > 0) else 0
    if max_dmps_norm and len(dmp_df) > max_dmps_norm:
        dmp_df = dmp_df.sort_values(["effect_size"], ascending=[False]).head(max_dmps_norm).copy()
    refs, feature_order = _build_reference_map(dmp_df)
    feature_family_set_norm = normalize_feature_family_set(feature_family_set)
    if family_includes_structural_scored(feature_family_set_norm):
        preflight_structural_scored_training(
            dmp_df=dmp_df,
            feature_order=feature_order,
            fixed_gene_features_df=fixed_gene_features_df,
            feature_family_set=feature_family_set_norm,
            structural_scored_min_support_n=int(structural_scored_min_support_n),
            region_directional_min_loci=int(max(1, region_directional_min_loci)),
            region_directional_region_types=region_directional_region_types,
        )

    roles = resolve_class_roles(project)
    class_names = list(roles["class_names"])
    resolved = project.get_resolved_groups()
    class_centroid_dirs = _resolve_class_centroid_dirs(project, class_names)
    healthy_idx_for_schema = int(roles["control_class_index"])
    schema_cancer_labels = [
        str(name) for i, name in enumerate(class_names) if int(i) != int(healthy_idx_for_schema)
    ]
    all_paths: List[str] = []
    y: List[int] = []
    sample_ids: List[str] = []
    for cls_idx, (_label, paths) in enumerate(resolved):
        for p in paths:
            all_paths.append(str(p))
            y.append(cls_idx)
            sample_ids.append(Path(str(p)).name)
    assert_model_mc_train_partition(project_json, train_sample_paths=all_paths)
    dropped_train_ids: List[str] = []
    if covariates_path:
        kept_ids, dropped_train_ids = resolve_covariate_sample_ids(
            sample_ids,
            covariates_path,
            covariate_id_column,
            missing_samples=covariates_missing_samples,
            strict_join=covariates_strict_join,
        )
        if dropped_train_ids:
            keep = set(kept_ids)
            mask = [sid in keep for sid in sample_ids]
            all_paths = [path for path, keep_row in zip(all_paths, mask) if keep_row]
            y = [label for label, keep_row in zip(y, mask) if keep_row]
            sample_ids = list(kept_ids)

    feature_mode_norm = str(feature_mode or "raw_dmp").strip().lower()
    gene_feature_loading_norm = str(gene_feature_loading or "frozen").strip().lower()
    if gene_feature_loading_norm not in {"frozen", "range"}:
        raise ValueError(
            f"gene_feature_loading must be one of ['frozen', 'range'], got {gene_feature_loading!r}"
        )
    y_arr = np.asarray(y, dtype=np.int32)
    dmp_index_fingerprint = _dmp_index_fingerprint(dmp_df)
    bundle_dir_path: Optional[Path] = None
    if bundle_dir:
        bundle_dir_path = Path(bundle_dir).expanduser().resolve()
    else:
        bundle_h5_parent = Path(bundle_h5).expanduser().resolve().parent
        if bundle_h5_parent:
            bundle_dir_path = bundle_h5_parent
    train_dataset_out_path = (
        Path(train_dataset_path).expanduser().resolve()
        if (save_train_dataset and train_dataset_path)
        else (
            (bundle_dir_path / TRAIN_DATASET_NAME)
            if (save_train_dataset and bundle_dir_path is not None)
            else ((out_dir / TRAIN_DATASET_NAME) if save_train_dataset else None)
        )
    )
    train_dataset_meta_path = _dataset_meta_path(train_dataset_out_path) if train_dataset_out_path is not None else None
    train_cache_enabled = bool(save_train_dataset and reuse_train_dataset and train_dataset_out_path is not None)
    train_cache_hit = False
    train_cache_miss_reason: Optional[str] = None
    feature_names: List[str] = []
    preprocessor: Optional[CovariatePreprocessor] = None
    cov_report: Dict[str, Any] = {"used": False, **exclusion_report(dropped_train_ids)}
    observed_feature_names: List[str] = []
    training_feature_names_obs: List[str] = []
    quality_feature_names: List[str] = []
    observed_feature_report: Dict[str, Any] = {}
    observed_feature_quantiles_out = [float(q) for q in (observed_feature_quantiles or [])]
    observed_healthy_reference: Optional[np.ndarray] = None
    observed_cancer_reference: Optional[np.ndarray] = None
    observed_per_cancer_references: List[np.ndarray] = []
    observed_healthy_class_index: Optional[int] = None
    observed_healthy_class_label: Optional[str] = None
    observed_cancer_class_labels: List[str] = []
    observed_anchor_strategy: Optional[str] = None
    observed_feature_order_fingerprint: Optional[str] = None
    gene_scored_progression_order: Optional[List[str]] = None
    structural_scored_progression_order: Optional[List[str]] = None
    feature_fill_values: Optional[np.ndarray] = None
    X = np.zeros((0, 0), dtype=np.float32)

    fingerprint_common_payload: Dict[str, Any] = {
        "schema_version": 1,
        "feature_mode": feature_mode_norm,
        "feature_family_set": feature_family_set_norm,
        "gene_feature_loading": gene_feature_loading_norm,
        "observed_hybrid_schema_version": (
            OBSERVED_HYBRID_SCHEMA_VERSION if feature_mode_norm == "observed_hybrid" else None
        ),
        "observed_hybrid_schema_fingerprint": (
            observed_hybrid_schema_fingerprint(
                cancer_class_labels=schema_cancer_labels,
                all_class_labels=class_names,
                feature_family_set=feature_family_set_norm,
                dmp_df=dmp_df,
                frozen_gene_panel_df=frozen_gene_panel_df,
                fixed_gene_features_df=fixed_gene_features_df,
                feature_order=feature_order,
                gene_scored_min_support_n=int(gene_scored_min_support_n),
                gene_scored_use_region_weight=bool(gene_scored_use_region_weight),
                gene_scored_gene_weight=str(gene_scored_gene_weight),
                gene_scored_ordered_comparison_labels=gene_scored_ordered_comparison_labels,
                gene_scored_contrast_pairs=gene_scored_contrast_pairs,
                structural_scored_min_support_n=int(structural_scored_min_support_n),
                structural_scored_use_region_weight=bool(structural_scored_use_region_weight),
                structural_scored_weight=str(structural_scored_weight),
                structural_scored_ordered_comparison_labels=structural_scored_ordered_comparison_labels,
                structural_scored_contrast_pairs=structural_scored_contrast_pairs,
                project_json=project_json,
                region_directional_region_types=region_directional_region_types,
                region_directional_min_loci=int(max(1, region_directional_min_loci)),
                observed_feature_quality_columns=observed_feature_quality_columns,
                **observed_chromosome_build_kwargs(
                    chromosome_hypo_beta_threshold=chromosome_hypo_beta_threshold,
                    chromosome_intermediate_beta_lo=chromosome_intermediate_beta_lo,
                    chromosome_intermediate_beta_hi=chromosome_intermediate_beta_hi,
                    chromosome_distance_metrics=chromosome_distance_metrics,
                    chromosome_list=chromosome_list,
                ),
            )
            if feature_mode_norm == "observed_hybrid"
            else None
        ),
        "dmp_index_fingerprint": dmp_index_fingerprint,
        "max_dmps": int(max_dmps_norm),
        "covariates_path": str(covariates_path) if covariates_path else None,
        "covariate_id_column": str(covariate_id_column),
        "covariates_strict_join": bool(covariates_strict_join),
        "covariates_missing_samples": covariates_missing_samples,
        "covariate_numeric_columns": [str(x) for x in (covariate_numeric_columns or [])],
        "covariate_ordinal_columns": [str(x) for x in (covariate_ordinal_columns or [])],
        "covariate_ordinal_maps": covariate_ordinal_maps or {},
        "covariate_ordinal_unknown_value": float(covariate_ordinal_unknown_value),
        "covariate_categorical_columns": [str(x) for x in (covariate_categorical_columns or [])],
        "covariate_missing_numeric_strategy": str(covariate_missing_numeric_strategy),
        "covariate_standardize_numeric": bool(covariate_standardize_numeric),
        "observed_feature_quantiles": [float(q) for q in (observed_feature_quantiles or [])],
        "observed_feature_min_coverage": int(max(1, observed_feature_min_coverage)),
        "observed_feature_include_dmp": bool(observed_feature_include_dmp),
        "observed_feature_include_chromosome": bool(observed_feature_include_chromosome),
        "observed_feature_include_dmr": bool(observed_feature_include_dmr),
        "observed_feature_include_gene": bool(observed_feature_include_gene),
        "observed_feature_dmr_window_bp": int(max(1, observed_feature_dmr_window_bp)),
        "observed_feature_max_dmrs": int(max(0, observed_feature_max_dmrs)),
        "observed_feature_max_genes": int(max(0, observed_feature_max_genes)),
        "observed_hist_eps": float(observed_hist_eps),
        "observed_hist_alpha": float(observed_hist_alpha),
        "observed_hist_evidence_clip_cap": float(observed_hist_evidence_clip_cap),
        "observed_hist_tail_agreement_threshold": float(observed_hist_tail_agreement_threshold),
        "gene_scored_min_support_n": int(max(1, gene_scored_min_support_n)),
        "gene_scored_use_region_weight": bool(gene_scored_use_region_weight),
        "gene_scored_gene_weight": str(gene_scored_gene_weight).strip().lower(),
        "gene_scored_ordered_comparison_labels": (
            [str(x) for x in gene_scored_ordered_comparison_labels]
            if gene_scored_ordered_comparison_labels
            else None
        ),
        "gene_scored_contrast_pairs": gene_scored_contrast_pairs,
        "structural_scored_min_support_n": int(max(1, structural_scored_min_support_n)),
        "structural_scored_use_region_weight": bool(structural_scored_use_region_weight),
        "structural_scored_weight": str(structural_scored_weight).strip().lower(),
        "structural_scored_ordered_comparison_labels": (
            [str(x) for x in structural_scored_ordered_comparison_labels]
            if structural_scored_ordered_comparison_labels
            else None
        ),
        "structural_scored_contrast_pairs": structural_scored_contrast_pairs,
        "region_directional_region_types": [
            str(x) for x in (region_directional_region_types or list(DEFAULT_REGION_DIRECTIONAL_TYPES))
        ],
        "region_directional_min_loci": int(max(1, region_directional_min_loci)),
        "observed_feature_quality_columns": [
            str(x) for x in (observed_feature_quality_columns or ["obs_fraction", "n_obs_dmps", "n_total_dmps"])
        ],
        "chromosome_hypo_beta_threshold": chromosome_hypo_beta_threshold,
        "chromosome_intermediate_beta_lo": chromosome_intermediate_beta_lo,
        "chromosome_intermediate_beta_hi": chromosome_intermediate_beta_hi,
        "chromosome_distance_metrics": (
            [str(x) for x in chromosome_distance_metrics] if chromosome_distance_metrics else None
        ),
        "chromosome_list": ([str(x) for x in chromosome_list] if chromosome_list else None),
    }
    train_fingerprint = _fingerprint_payload(
        {
            "common": fingerprint_common_payload,
            "split": "train",
            "sample_paths": all_paths,
            "class_names": class_names,
            "labels": [int(v) for v in y],
        }
    )

    if train_cache_enabled and train_dataset_out_path is not None and train_dataset_meta_path is not None:
        if train_dataset_out_path.is_file() and train_dataset_meta_path.is_file():
            try:
                with open(train_dataset_meta_path, encoding="utf-8") as f:
                    train_meta = json.load(f)
                if train_meta.get("fingerprint") != train_fingerprint:
                    train_cache_miss_reason = "fingerprint_mismatch"
                else:
                    train_df = _read_dataset_frame(train_dataset_out_path)
                    X, y_arr, sample_ids, _cached_labels, feature_names = _dataset_to_matrix(train_df)
                    preproc_payload = train_meta.get("covariate_preprocessor")
                    preprocessor = (
                        CovariatePreprocessor.from_dict(preproc_payload)
                        if isinstance(preproc_payload, dict)
                        else None
                    )
                    cov_report = (
                        dict(train_meta.get("covariate_report"))
                        if isinstance(train_meta.get("covariate_report"), dict)
                        else {"used": bool(preprocessor is not None)}
                    )
                    observed_feature_names = [str(x) for x in (train_meta.get("observed_feature_names") or [])]
                    training_feature_names_obs = [
                        str(x) for x in (train_meta.get("training_feature_names") or [])
                    ]
                    quality_feature_names = [str(x) for x in (train_meta.get("quality_feature_names") or [])]
                    observed_feature_report = (
                        dict(train_meta.get("observed_feature_report"))
                        if isinstance(train_meta.get("observed_feature_report"), dict)
                        else {}
                    )
                    observed_feature_quantiles_out = [
                        float(x) for x in (train_meta.get("observed_feature_quantiles") or [])
                    ]
                    fill_vals = train_meta.get("observed_feature_fill_values")
                    feature_fill_values = (
                        np.asarray(fill_vals, dtype=np.float32)
                        if isinstance(fill_vals, list) and len(fill_vals) > 0
                        else None
                    )
                    href = train_meta.get("observed_healthy_reference_vector")
                    observed_healthy_reference = (
                        np.asarray(href, dtype=np.float32) if isinstance(href, list) and len(href) > 0 else None
                    )
                    cref = train_meta.get("observed_cancer_reference_vector")
                    observed_cancer_reference = (
                        np.asarray(cref, dtype=np.float32) if isinstance(cref, list) and len(cref) > 0 else None
                    )
                    pc_refs = train_meta.get("observed_per_cancer_reference_vectors")
                    observed_per_cancer_references = []
                    if isinstance(pc_refs, list):
                        for item in pc_refs:
                            if isinstance(item, list) and len(item) > 0:
                                observed_per_cancer_references.append(np.asarray(item, dtype=np.float32))
                    observed_healthy_class_index = train_meta.get("observed_healthy_class_index")
                    observed_healthy_class_label = (
                        str(train_meta["observed_healthy_class_label"])
                        if train_meta.get("observed_healthy_class_label") is not None
                        else None
                    )
                    observed_cancer_class_labels = [
                        str(x) for x in (train_meta.get("observed_cancer_class_labels") or [])
                    ]
                    observed_anchor_strategy = (
                        str(train_meta["observed_anchor_strategy"])
                        if train_meta.get("observed_anchor_strategy") is not None
                        else None
                    )
                    observed_feature_order_fingerprint = (
                        str(train_meta["observed_feature_order_fingerprint"])
                        if train_meta.get("observed_feature_order_fingerprint") is not None
                        else None
                    )
                    raw_prog_order = train_meta.get("gene_scored_progression_order")
                    if isinstance(raw_prog_order, list) and raw_prog_order:
                        gene_scored_progression_order = [str(x) for x in raw_prog_order]
                    if feature_mode_norm == "observed_hybrid":
                        if (
                            not observed_feature_names
                            or feature_fill_values is None
                            or observed_healthy_reference is None
                            or observed_cancer_reference is None
                        ):
                            raise ValueError("observed_hybrid cache metadata is incomplete")
                        verify_feature_schema(
                            observed_feature_names,
                            observed_hybrid_feature_names(
                                cancer_class_labels=observed_cancer_class_labels,
                                all_class_labels=class_names,
                                feature_family_set=feature_family_set_norm,
                                dmp_df=dmp_df,
                                frozen_gene_panel_df=frozen_gene_panel_df,
                                fixed_gene_features_df=fixed_gene_features_df,
                                feature_order=feature_order,
                                gene_scored_min_support_n=int(gene_scored_min_support_n),
                                gene_scored_ordered_comparison_labels=(
                                    gene_scored_progression_order or gene_scored_ordered_comparison_labels
                                ),
                                gene_scored_contrast_pairs=gene_scored_contrast_pairs,
                                structural_scored_min_support_n=int(structural_scored_min_support_n),
                                structural_scored_ordered_comparison_labels=(
                                    structural_scored_progression_order
                                    or structural_scored_ordered_comparison_labels
                                ),
                                structural_scored_contrast_pairs=structural_scored_contrast_pairs,
                                project_json=project_json,
                                region_directional_region_types=region_directional_region_types,
                                region_directional_min_loci=int(max(1, region_directional_min_loci)),
                                observed_feature_quality_columns=observed_feature_quality_columns,
                            ),
                            context="tabular train cached observed_hybrid",
                        )
                        if not training_feature_names_obs:
                            quality_set = set(quality_feature_names)
                            if not quality_set:
                                quality_set = {"obs_fraction", "n_obs_dmps", "n_total_dmps"}
                            training_feature_names_obs = [
                                str(n) for n in observed_feature_names if str(n) not in quality_set
                            ]
                        cov_cols = [str(n) for n in feature_names if str(n) not in set(observed_feature_names)]
                        model_cols = list(training_feature_names_obs) + cov_cols
                        idx_map = {str(n): int(i) for i, n in enumerate(feature_names)}
                        keep = [idx_map[n] for n in model_cols if n in idx_map]
                        if keep:
                            X = X[:, keep]
                            feature_names = [str(n) for n in model_cols if str(n) in idx_map]
                    train_cache_hit = True
            except Exception as e:
                train_cache_miss_reason = f"cache_read_error:{e}"
        else:
            train_cache_miss_reason = "cache_missing"

    if not train_cache_hit:
        X_export: Optional[np.ndarray] = None
        if feature_mode_norm == "observed_hybrid":
            anchors = derive_observed_hybrid_anchors(
                all_paths,
                y,
                class_names,
                dmp_df,
                min_coverage=int(max(1, observed_feature_min_coverage)),
                feature_family_set=feature_family_set_norm,
                gene_feature_loading=gene_feature_loading_norm,
                fixed_gene_features_df=fixed_gene_features_df,
            )
            feat = build_observed_hybrid_feature_table(
                all_paths,
                dmp_df,
                quantiles=observed_feature_quantiles,
                min_coverage=int(max(1, observed_feature_min_coverage)),
                include_dmp_features=bool(observed_feature_include_dmp),
                include_chromosome_features=bool(observed_feature_include_chromosome),
                include_dmr_features=bool(observed_feature_include_dmr),
                include_gene_features=bool(observed_feature_include_gene),
                dmr_window_bp=int(max(1, observed_feature_dmr_window_bp)),
                max_dmr_features=int(max(0, observed_feature_max_dmrs)),
                max_gene_features=int(max(0, observed_feature_max_genes)),
                healthy_reference_vector=anchors.healthy_reference_vector,
                cancer_reference_vector=anchors.cancer_reference_vector,
                per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
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
                feature_family_set=feature_family_set_norm,
                gene_feature_loading=gene_feature_loading_norm,
                fixed_gene_features_df=fixed_gene_features_df,
                frozen_gene_panel_df=frozen_gene_panel_df,
                gene_scored_min_support_n=int(gene_scored_min_support_n),
                gene_scored_use_region_weight=bool(gene_scored_use_region_weight),
                gene_scored_gene_weight=str(gene_scored_gene_weight),
                gene_scored_ordered_comparison_labels=gene_scored_ordered_comparison_labels,
                gene_scored_contrast_pairs=gene_scored_contrast_pairs,
                structural_scored_min_support_n=int(structural_scored_min_support_n),
                structural_scored_use_region_weight=bool(structural_scored_use_region_weight),
                structural_scored_weight=str(structural_scored_weight),
                structural_scored_ordered_comparison_labels=structural_scored_ordered_comparison_labels,
                structural_scored_contrast_pairs=structural_scored_contrast_pairs,
                project_json=project_json,
                region_directional_region_types=region_directional_region_types,
                region_directional_min_loci=int(max(1, region_directional_min_loci)),
                observed_feature_quality_columns=observed_feature_quality_columns,
                **observed_chromosome_build_kwargs(
                    chromosome_hypo_beta_threshold=chromosome_hypo_beta_threshold,
                    chromosome_intermediate_beta_lo=chromosome_intermediate_beta_lo,
                    chromosome_intermediate_beta_hi=chromosome_intermediate_beta_hi,
                    chromosome_distance_metrics=chromosome_distance_metrics,
                    chromosome_list=chromosome_list,
                ),
            )
            X_obs_full = np.asarray(feat.X, dtype=np.float32)
            feature_fill_values = fit_feature_fill_values(X_obs_full)
            X_obs_full = apply_feature_fill_values(X_obs_full, feature_fill_values)
            observed_feature_names = list(feat.feature_names)
            training_feature_names_obs = list(feat.training_feature_names)
            quality_feature_names = list(feat.quality_feature_names)
            X = select_training_feature_matrix(
                X_obs_full,
                observed_feature_names,
                training_feature_names_obs,
            )
            observed_feature_report = dict(feat.report)
            gene_scored_progression_order = None
            structural_scored_progression_order = None
            gene_scored_report = observed_feature_report.get("gene_scored")
            if isinstance(gene_scored_report, dict):
                raw_order = gene_scored_report.get("progression_order")
                if isinstance(raw_order, list) and raw_order:
                    gene_scored_progression_order = [str(x) for x in raw_order]
            structural_scored_report = observed_feature_report.get("structural_scored")
            if isinstance(structural_scored_report, dict):
                raw_order = structural_scored_report.get("progression_order")
                if isinstance(raw_order, list) and raw_order:
                    structural_scored_progression_order = [str(x) for x in raw_order]
            observed_feature_quantiles_out = [float(q) for q in (feat.report.get("quantiles") or [])]
            observed_healthy_reference = anchors.healthy_reference_vector.astype(np.float32)
            observed_cancer_reference = anchors.cancer_reference_vector.astype(np.float32)
            observed_per_cancer_references = [
                np.asarray(v, dtype=np.float32) for v in anchors.per_cancer_reference_vectors
            ]
            observed_healthy_class_index = int(anchors.healthy_class_index)
            observed_healthy_class_label = str(anchors.healthy_class_label)
            observed_cancer_class_labels = [str(x) for x in anchors.cancer_class_labels]
            observed_anchor_strategy = str(anchors.anchor_strategy)
            observed_feature_order_fingerprint = str(anchors.feature_order_fingerprint)
        else:
            X = _extract_matrix_for_samples(all_paths, refs, feature_order, min_coverage=1)
            X = np.asarray(X, dtype=np.float32)
            X = np.nan_to_num(X, nan=0.5, posinf=0.5, neginf=0.5)

        composition_specs = normalize_composition_groups(
            [
                g.model_dump() if hasattr(g, "model_dump") else dict(g)
                for g in (covariate_composition_groups or [])
            ]
            or None,
            legacy_transform=covariate_composition_transform,
            legacy_columns=covariate_composition_columns,
            legacy_reference=covariate_composition_reference,
            legacy_pseudocount=covariate_composition_pseudocount,
        )
        cov, preprocessor, cov_report = fit_covariates(
            covariates_path,
            sample_ids,
            covariate_id_column=covariate_id_column,
            strict_join=True if dropped_train_ids else covariates_strict_join,
            numeric_columns=covariate_numeric_columns,
            ordinal_columns=covariate_ordinal_columns,
            ordinal_maps=covariate_ordinal_maps,
            ordinal_unknown_value=covariate_ordinal_unknown_value,
            categorical_columns=covariate_categorical_columns,
            missing_numeric_strategy=covariate_missing_numeric_strategy,
            standardize_numeric=covariate_standardize_numeric,
            composition_groups=composition_specs,
        )
        cov_report = dict(cov_report or {"used": False})
        cov_report.update(exclusion_report(dropped_train_ids))
        if feature_mode_norm == "observed_hybrid":
            X_export = X_obs_full
        if cov is not None:
            X = np.concatenate([X, cov], axis=1)
            if feature_mode_norm == "observed_hybrid":
                X_export = np.concatenate([X_obs_full, cov], axis=1)

        if feature_mode_norm == "observed_hybrid":
            export_base_feature_names = list(observed_feature_names)
            model_base_feature_names = list(training_feature_names_obs)
        else:
            export_base_feature_names = [f"{c}:{ctx}:{int(pos)}" for c, ctx, pos in feature_order]
            model_base_feature_names = export_base_feature_names
        cov_feature_names = list(preprocessor.output_columns) if preprocessor is not None else []
        export_feature_names = export_base_feature_names + cov_feature_names
        model_feature_names = model_base_feature_names + cov_feature_names
        export_matrix = X_export if X_export is not None else X
        if len(export_feature_names) != int(export_matrix.shape[1]):
            export_feature_names = [f"feature_{i}" for i in range(int(export_matrix.shape[1]))]
        expected_n_features = len(model_feature_names)
        if expected_n_features != int(X.shape[1]):
            feature_names = [f"feature_{i}" for i in range(int(X.shape[1]))]
        else:
            feature_names = model_feature_names

        if save_train_dataset and train_dataset_out_path is not None and train_dataset_meta_path is not None:
            train_export_df = pd.DataFrame(export_matrix, columns=export_feature_names)
            train_export_df.insert(0, "sample_id", sample_ids)
            train_export_df.insert(1, "class_index", y_arr.astype(int))
            train_export_df.insert(2, "class_label", [class_names[int(v)] for v in y_arr.tolist()])
            _write_dataset_frame(train_dataset_out_path, train_export_df)
            train_dataset_meta = {
                "split": "train",
                "fingerprint": train_fingerprint,
                "feature_names": feature_names,
                "class_names": class_names,
                "covariate_preprocessor": preprocessor.to_dict() if preprocessor is not None else None,
                "covariate_report": cov_report,
                "observed_feature_names": observed_feature_names,
                "training_feature_names": training_feature_names_obs,
                "quality_feature_names": quality_feature_names,
                "observed_feature_report": observed_feature_report,
                "observed_feature_quantiles": observed_feature_quantiles_out,
                "observed_feature_fill_values": (
                    [float(v) for v in feature_fill_values.tolist()] if feature_fill_values is not None else None
                ),
                "observed_healthy_reference_vector": (
                    [float(v) for v in observed_healthy_reference.tolist()]
                    if observed_healthy_reference is not None
                    else None
                ),
                "observed_cancer_reference_vector": (
                    [float(v) for v in observed_cancer_reference.tolist()]
                    if observed_cancer_reference is not None
                    else None
                ),
                "observed_per_cancer_reference_vectors": [
                    [float(v) for v in vec.tolist()] for vec in observed_per_cancer_references
                ],
                "observed_healthy_class_index": observed_healthy_class_index,
                "observed_healthy_class_label": observed_healthy_class_label,
                "observed_cancer_class_labels": observed_cancer_class_labels,
                "observed_anchor_strategy": observed_anchor_strategy,
                "observed_feature_order_fingerprint": observed_feature_order_fingerprint,
                "observed_hist_eps": float(observed_hist_eps),
                "observed_hist_alpha": float(observed_hist_alpha),
                "observed_hist_evidence_clip_cap": float(observed_hist_evidence_clip_cap),
                "observed_hist_tail_agreement_threshold": float(observed_hist_tail_agreement_threshold),
                "gene_scored_min_support_n": int(max(1, gene_scored_min_support_n)),
                "gene_scored_use_region_weight": bool(gene_scored_use_region_weight),
                "gene_scored_gene_weight": str(gene_scored_gene_weight).strip().lower(),
                "gene_scored_ordered_comparison_labels": (
                    [str(x) for x in gene_scored_ordered_comparison_labels]
                    if gene_scored_ordered_comparison_labels
                    else None
                ),
                "gene_scored_contrast_pairs": gene_scored_contrast_pairs,
                "gene_scored_progression_order": gene_scored_progression_order,
                "structural_scored_min_support_n": int(max(1, structural_scored_min_support_n)),
                "structural_scored_use_region_weight": bool(structural_scored_use_region_weight),
                "structural_scored_weight": str(structural_scored_weight).strip().lower(),
                "structural_scored_ordered_comparison_labels": (
                    [str(x) for x in structural_scored_ordered_comparison_labels]
                    if structural_scored_ordered_comparison_labels
                    else None
                ),
                "structural_scored_contrast_pairs": structural_scored_contrast_pairs,
                "structural_scored_progression_order": structural_scored_progression_order,
                "region_directional_region_types": [
                    str(x)
                    for x in (region_directional_region_types or list(DEFAULT_REGION_DIRECTIONAL_TYPES))
                ],
                "region_directional_min_loci": int(max(1, region_directional_min_loci)),
            }
            with open(train_dataset_meta_path, "w", encoding="utf-8") as f:
                json.dump(train_dataset_meta, f, indent=2)

    test_dataset_out_path: Optional[Path] = None
    test_cache_hit: Optional[bool] = None
    test_cache_miss_reason: Optional[str] = None
    if save_test_dataset:
        # Model-MC holdout only: never use legacy predictor test_* paths (often
        # misnamed train cohorts). Same contract as tabular predict scoring.
        test_dataset_out_path = _derive_test_dataset_path(
            train_dataset_out_path,
            test_dataset_path,
            out_dir,
            bundle_dir_path,
        )
        test_meta_path = _dataset_meta_path(test_dataset_out_path)
        eval_paths, eval_y = resolve_eval_paths_and_labels(
            project_json,
            class_names,
            project_loader=load_project,
            evaluation_partition="test",
        )
        if eval_y is None:
            raise ValueError("No labeled evaluation samples resolved for tabular test dataset export.")
        eval_ids = sample_ids_from_paths(eval_paths)
        dropped_eval_ids: List[str] = []
        if covariates_path:
            kept_eval_ids, dropped_eval_ids = resolve_covariate_sample_ids(
                eval_ids,
                covariates_path,
                covariate_id_column,
                missing_samples=covariates_missing_samples,
                strict_join=covariates_strict_join,
            )
            if dropped_eval_ids:
                keep_eval = set(kept_eval_ids)
                eval_mask = [sid in keep_eval for sid in eval_ids]
                eval_paths = [path for path, keep_row in zip(eval_paths, eval_mask) if keep_row]
                eval_y = [label for label, keep_row in zip(list(eval_y), eval_mask) if keep_row]
                eval_ids = list(kept_eval_ids)
        test_fingerprint = _fingerprint_payload(
            {
                "common": fingerprint_common_payload,
                "split": "test",
                "sample_paths": [str(p) for p in eval_paths],
                "class_names": class_names,
                "labels": [int(v) for v in np.asarray(eval_y, dtype=np.int32).tolist()],
                "train_fingerprint": train_fingerprint,
            }
        )
        if test_dataset_out_path.is_file() and test_meta_path.is_file():
            try:
                with open(test_meta_path, encoding="utf-8") as f:
                    test_meta = json.load(f)
                if test_meta.get("fingerprint") == test_fingerprint:
                    test_cache_hit = True
                else:
                    test_cache_hit = False
                    test_cache_miss_reason = "fingerprint_mismatch"
            except Exception as e:
                test_cache_hit = False
                test_cache_miss_reason = f"cache_read_error:{e}"
        else:
            test_cache_hit = False
            test_cache_miss_reason = "cache_missing"
        if not test_cache_hit:
            if feature_mode_norm == "observed_hybrid":
                if observed_healthy_reference is None or observed_cancer_reference is None:
                    raise ValueError("Observed-hybrid test export requires training reference vectors.")
                feat_eval = build_observed_hybrid_feature_table(
                    eval_paths,
                    dmp_df,
                    quantiles=observed_feature_quantiles_out or observed_feature_quantiles,
                    min_coverage=int(max(1, observed_feature_min_coverage)),
                    include_dmp_features=bool(observed_feature_include_dmp),
                    include_chromosome_features=bool(observed_feature_include_chromosome),
                    include_dmr_features=bool(observed_feature_include_dmr),
                    include_gene_features=bool(observed_feature_include_gene),
                    dmr_window_bp=int(max(1, observed_feature_dmr_window_bp)),
                    max_dmr_features=int(max(0, observed_feature_max_dmrs)),
                    max_gene_features=int(max(0, observed_feature_max_genes)),
                    healthy_reference_vector=observed_healthy_reference,
                    cancer_reference_vector=observed_cancer_reference,
                    per_cancer_reference_vectors=observed_per_cancer_references,
                    healthy_class_label=observed_healthy_class_label,
                    cancer_class_labels=observed_cancer_class_labels,
                    all_class_labels=class_names,
                    anchor_strategy=observed_anchor_strategy,
                    expected_feature_order_fingerprint=observed_feature_order_fingerprint,
                    centroid_dir_by_class_label=class_centroid_dirs,
                    hist_eps=float(observed_hist_eps),
                    hist_alpha=float(observed_hist_alpha),
                    hist_evidence_clip_cap=float(observed_hist_evidence_clip_cap),
                    hist_tail_agreement_threshold=float(observed_hist_tail_agreement_threshold),
                    feature_family_set=feature_family_set_norm,
                    gene_feature_loading=gene_feature_loading_norm,
                    fixed_gene_features_df=fixed_gene_features_df,
                    frozen_gene_panel_df=frozen_gene_panel_df,
                    gene_scored_min_support_n=int(gene_scored_min_support_n),
                    gene_scored_use_region_weight=bool(gene_scored_use_region_weight),
                    gene_scored_gene_weight=str(gene_scored_gene_weight),
                    gene_scored_ordered_comparison_labels=gene_scored_ordered_comparison_labels,
                    gene_scored_contrast_pairs=gene_scored_contrast_pairs,
                    structural_scored_min_support_n=int(structural_scored_min_support_n),
                    structural_scored_use_region_weight=bool(structural_scored_use_region_weight),
                    structural_scored_weight=str(structural_scored_weight),
                    structural_scored_ordered_comparison_labels=structural_scored_ordered_comparison_labels,
                    structural_scored_contrast_pairs=structural_scored_contrast_pairs,
                    project_json=project_json,
                    region_directional_region_types=region_directional_region_types,
                    region_directional_min_loci=int(max(1, region_directional_min_loci)),
                    observed_feature_quality_columns=observed_feature_quality_columns,
                    **observed_chromosome_build_kwargs(
                        chromosome_hypo_beta_threshold=chromosome_hypo_beta_threshold,
                        chromosome_intermediate_beta_lo=chromosome_intermediate_beta_lo,
                        chromosome_intermediate_beta_hi=chromosome_intermediate_beta_hi,
                        chromosome_distance_metrics=chromosome_distance_metrics,
                        chromosome_list=chromosome_list,
                    ),
                )
                verify_feature_schema(
                    feat_eval.feature_names,
                    observed_feature_names,
                    context="tabular train test-dataset export observed_hybrid",
                )
                X_eval_full = np.asarray(feat_eval.X, dtype=np.float32)
                X_eval_full = apply_feature_fill_values(X_eval_full, feature_fill_values.tolist())
                X_eval = select_training_feature_matrix(
                    X_eval_full,
                    feat_eval.feature_names,
                    training_feature_names_obs,
                )
            else:
                X_eval = _extract_matrix_for_samples(eval_paths, refs, feature_order, min_coverage=1)
                X_eval = np.nan_to_num(np.asarray(X_eval, dtype=np.float32), nan=0.5, posinf=0.5, neginf=0.5)
            cov_eval, _cov_eval_report = transform_covariates(
                covariates_path,
                eval_ids,
                preprocessor,
                strict_join=True if dropped_eval_ids else bool(covariates_strict_join),
            )
            if cov_eval is not None:
                X_eval = np.concatenate([X_eval, cov_eval], axis=1)
            test_feature_names = list(feature_names)
            if len(test_feature_names) != int(X_eval.shape[1]):
                test_feature_names = [f"feature_{i}" for i in range(int(X_eval.shape[1]))]
            eval_df = pd.DataFrame(X_eval, columns=test_feature_names)
            eval_y_arr = np.asarray(eval_y, dtype=np.int32)
            eval_df.insert(0, "sample_id", eval_ids)
            eval_df.insert(1, "class_index", eval_y_arr.astype(int))
            eval_df.insert(2, "class_label", [class_names[int(v)] for v in eval_y_arr.tolist()])
            _write_dataset_frame(test_dataset_out_path, eval_df)
            with open(test_meta_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "split": "test",
                        "fingerprint": test_fingerprint,
                        "feature_names": test_feature_names,
                        "class_names": class_names,
                    },
                    f,
                    indent=2,
                )

    if (
        save_train_dataset
        and train_dataset_out_path is not None
        and train_dataset_out_path.is_file()
    ):
        manifest_bundle = (
            bundle_dir_path
            if bundle_dir_path is not None
            else train_dataset_out_path.parent
        )
        overlap_ids: List[str] = []
        if (
            test_dataset_out_path is not None
            and test_dataset_out_path.is_file()
        ):
            train_ids = set(
                _read_dataset_frame(train_dataset_out_path)["sample_id"].astype(str)
            )
            test_ids = set(
                _read_dataset_frame(test_dataset_out_path)["sample_id"].astype(str)
            )
            overlap_ids = sorted(train_ids & test_ids)
            if overlap_ids:
                raise ValueError(
                    "Tabular train/test datasets overlap; refusing to export model_bundle. "
                    f"First overlap(s): {overlap_ids[:5]}"
                )
        write_dataset_manifest(
            manifest_bundle,
            backend="tabular_sklearn",
            train_path=train_dataset_out_path,
            test_path=(
                test_dataset_out_path
                if test_dataset_out_path is not None and test_dataset_out_path.is_file()
                else None
            ),
            feature_columns=list(feature_names) if feature_names else None,
            train_test_overlap_count=(
                int(len(overlap_ids))
                if (
                    test_dataset_out_path is not None
                    and test_dataset_out_path.is_file()
                )
                else None
            ),
            overlapping_sample_ids=overlap_ids,
        )

    _require_non_empty_training_matrix(
        X,
        feature_mode=feature_mode_norm,
        feature_family_set=feature_family_set_norm,
    )
    methods = _normalize_tabular_methods(tabular_methods, legacy_model_type=model_type)
    run_selection_eval = len(methods) > 1
    method_rows: List[Dict[str, Any]] = []
    selected_idx = 0
    models_root = out_dir / "tabular_methods"
    models_root.mkdir(parents=True, exist_ok=True)
    selection_metric = str(tabular_method_selection_metric or "balanced_accuracy").strip().lower()
    selection_stat = str(tabular_method_selection_stat or "mean").strip().lower()
    best_score = float("-inf")
    for idx, method_cfg in enumerate(methods):
        estimator, resolved_params = _build_estimator_from_config(method_cfg)
        estimator.fit(X, y_arr)
        y_pred_train = np.asarray(estimator.predict(X), dtype=np.int32)
        method_name = str(method_cfg["method"])
        method_dir = models_root / f"{idx:02d}_{method_name}"
        method_dir.mkdir(parents=True, exist_ok=True)
        method_model_path = method_dir / "tabular-model.joblib"
        joblib.dump(estimator, method_model_path)
        train_metrics = compute_validation_metrics(
            y_arr, y_pred_train, class_names, class_roles=roles
        )
        train_metrics["metrics_source"] = "tabular_train"
        train_metrics["evaluation_split"] = "training"
        train_metrics["method"] = method_name
        train_metrics["method_index"] = int(idx)
        train_metrics["n_train_samples"] = int(len(y_arr))
        train_metrics["n_train_features"] = int(X.shape[1])
        with open(method_dir / "training_metrics.json", "w", encoding="utf-8") as f:
            json.dump(train_metrics, f, indent=2)
        method_preproc_path = method_dir / "covariate-preprocessor.json"
        if preprocessor is not None:
            preprocessor.save_json(method_preproc_path)
        method_meta = {
            "method": method_name,
            "params": resolved_params,
            "feature_mode": feature_mode_norm,
            "feature_family_set": feature_family_set_norm,
            "gene_feature_loading": gene_feature_loading_norm,
            "class_names": class_names,
            "class_roles": roles,
            "project_json": str(Path(project_json).resolve()),
            "bundle_h5": str(Path(bundle_h5).resolve()),
            "max_dmps": int(max_dmps_norm),
            "n_features": int(X.shape[1]),
            "n_dmps": int(len(feature_order)),
            "feature_order": [{"chromosome": c, "context": ctx, "position": int(pos)} for c, ctx, pos in feature_order],
            "observed_feature_names": observed_feature_names,
            "training_feature_names": training_feature_names_obs,
            "quality_feature_names": quality_feature_names,
            "observed_feature_quantiles": observed_feature_quantiles_out,
            "observed_feature_min_coverage": int(max(1, observed_feature_min_coverage)),
            "observed_feature_min_obs_fraction": float(max(0.0, min(1.0, observed_feature_min_obs_fraction))),
            "observed_feature_include_dmp": bool(observed_feature_include_dmp),
            "observed_feature_include_chromosome": bool(observed_feature_include_chromosome),
            "observed_feature_include_dmr": bool(observed_feature_include_dmr),
            "observed_feature_include_gene": bool(observed_feature_include_gene),
            "observed_feature_dmr_window_bp": int(max(1, observed_feature_dmr_window_bp)),
            "observed_feature_max_dmrs": int(max(0, observed_feature_max_dmrs)),
            "observed_feature_max_genes": int(max(0, observed_feature_max_genes)),
            "observed_feature_fill_values": (
                [float(v) for v in feature_fill_values.tolist()] if feature_fill_values is not None else None
            ),
            "observed_feature_report": observed_feature_report,
            "observed_healthy_reference_vector": (
                [float(v) for v in observed_healthy_reference.tolist()] if observed_healthy_reference is not None else None
            ),
            "observed_cancer_reference_vector": (
                [float(v) for v in observed_cancer_reference.tolist()] if observed_cancer_reference is not None else None
            ),
            "observed_per_cancer_reference_vectors": [
                [float(v) for v in vec.tolist()] for vec in observed_per_cancer_references
            ],
            "observed_healthy_class_index": observed_healthy_class_index,
            "observed_healthy_class_label": observed_healthy_class_label,
            "observed_cancer_class_labels": observed_cancer_class_labels,
            "observed_anchor_strategy": observed_anchor_strategy,
            "observed_feature_order_fingerprint": observed_feature_order_fingerprint,
            "observed_hist_eps": float(observed_hist_eps),
            "observed_hist_alpha": float(observed_hist_alpha),
            "observed_hist_evidence_clip_cap": float(observed_hist_evidence_clip_cap),
            "observed_hist_tail_agreement_threshold": float(observed_hist_tail_agreement_threshold),
            "gene_scored_min_support_n": int(max(1, gene_scored_min_support_n)),
            "gene_scored_use_region_weight": bool(gene_scored_use_region_weight),
            "gene_scored_gene_weight": str(gene_scored_gene_weight).strip().lower(),
            "gene_scored_ordered_comparison_labels": (
                [str(x) for x in gene_scored_ordered_comparison_labels]
                if gene_scored_ordered_comparison_labels
                else None
            ),
            "gene_scored_contrast_pairs": gene_scored_contrast_pairs,
            "gene_scored_progression_order": gene_scored_progression_order,
            "structural_scored_min_support_n": int(max(1, structural_scored_min_support_n)),
            "structural_scored_use_region_weight": bool(structural_scored_use_region_weight),
            "structural_scored_weight": str(structural_scored_weight).strip().lower(),
            "structural_scored_ordered_comparison_labels": (
                [str(x) for x in structural_scored_ordered_comparison_labels]
                if structural_scored_ordered_comparison_labels
                else None
            ),
            "structural_scored_contrast_pairs": structural_scored_contrast_pairs,
            "structural_scored_progression_order": structural_scored_progression_order,
            "region_directional_region_types": [
                str(x) for x in (region_directional_region_types or list(DEFAULT_REGION_DIRECTIONAL_TYPES))
            ],
            "region_directional_min_loci": int(max(1, region_directional_min_loci)),
            "observed_feature_quality_columns": [
                str(x) for x in (observed_feature_quality_columns or ["obs_fraction", "n_obs_dmps", "n_total_dmps"])
            ],
            "chromosome_hypo_beta_threshold": chromosome_hypo_beta_threshold,
            "chromosome_intermediate_beta_lo": chromosome_intermediate_beta_lo,
            "chromosome_intermediate_beta_hi": chromosome_intermediate_beta_hi,
            "chromosome_distance_metrics": (
                [str(x) for x in chromosome_distance_metrics] if chromosome_distance_metrics else None
            ),
            "chromosome_list": ([str(x) for x in chromosome_list] if chromosome_list else None),
            "selected_feature_count": int(len(feature_names)),
            "covariates_path": str(covariates_path) if covariates_path else None,
            "covariate_id_column": covariate_id_column,
            "covariates_strict_join": bool(covariates_strict_join),
            "covariates_missing_samples": covariates_missing_samples,
            "covariate_numeric_columns": [str(x) for x in (covariate_numeric_columns or [])],
            "covariate_ordinal_columns": [str(x) for x in (covariate_ordinal_columns or [])],
            "covariate_ordinal_maps": covariate_ordinal_maps or {},
            "covariate_ordinal_unknown_value": float(covariate_ordinal_unknown_value),
            "covariate_categorical_columns": [str(x) for x in (covariate_categorical_columns or [])],
            "covariate_missing_numeric_strategy": str(covariate_missing_numeric_strategy),
            "covariate_standardize_numeric": bool(covariate_standardize_numeric),
            "covariate_preprocessor_path": str(method_preproc_path) if preprocessor is not None else None,
            "covariate_preprocessing": cov_report,
            "train_dataset_saved": bool(save_train_dataset),
            "train_dataset_path": str(train_dataset_out_path) if train_dataset_out_path is not None else None,
            "train_dataset_cache_enabled": bool(train_cache_enabled),
            "train_dataset_cache_hit": bool(train_cache_hit) if train_cache_enabled else False,
            "train_dataset_cache_miss_reason": train_cache_miss_reason,
            "test_dataset_saved": bool(test_dataset_out_path is not None),
            "test_dataset_path": str(test_dataset_out_path) if test_dataset_out_path is not None else None,
            "test_dataset_cache_hit": test_cache_hit,
            "test_dataset_cache_miss_reason": test_cache_miss_reason,
        }
        with open(method_dir / "tabular-model-metadata.json", "w", encoding="utf-8") as f:
            json.dump(method_meta, f, indent=2)
        score = float("nan")
        if run_selection_eval:
            eval_out_dir = method_dir / "selection_eval"
            try:
                metrics = predict_tabular_model_from_project(
                    project_json=project_json,
                    model_dir=method_dir,
                    output_dir=eval_out_dir,
                    covariates_path=covariates_path,
                    covariate_id_column=covariate_id_column,
                    covariates_strict_join=covariates_strict_join,
                    observed_feature_min_obs_fraction=observed_feature_min_obs_fraction,
                )
                score = float(metrics.get(selection_metric, float("nan")))
            except Exception:
                score = float("nan")
        method_rows.append(
            {
                "method_index": int(idx),
                "method": method_name,
                "selection_metric": selection_metric,
                "selection_stat": selection_stat,
                "score": float(score) if np.isfinite(score) else float("-inf"),
                "train_balanced_accuracy": float(train_metrics.get("balanced_accuracy", float("nan"))),
                "robustness_score": (
                    float(score) - abs(float(score) - float(train_metrics.get("balanced_accuracy", 0.0)))
                    if np.isfinite(score)
                    else float("-inf")
                ),
                "model_dir": str(method_dir),
                "params_json": json.dumps(resolved_params, sort_keys=True),
            }
        )
        if np.isfinite(score) and score > best_score:
            best_score = float(score)
            selected_idx = int(idx)

    method_df = pd.DataFrame(method_rows)
    if run_selection_eval and not method_df.empty:
        if feature_mode_norm == "observed_hybrid":
            method_df.sort_values(
                ["robustness_score", "score", "method_index"],
                ascending=[False, False, True],
                inplace=True,
            )
        else:
            method_df.sort_values(["score", "method_index"], ascending=[False, True], inplace=True)
        method_df.reset_index(drop=True, inplace=True)
        method_df["rank"] = np.arange(1, len(method_df) + 1, dtype=int)
        method_df.to_csv(out_dir / "tabular_method_metrics.csv", index=False)
        with open(out_dir / "tabular_method_ranking.json", "w", encoding="utf-8") as f:
            json.dump(method_df.to_dict(orient="records"), f, indent=2)
        selected_idx = int(method_df.iloc[0]["method_index"])

    selected_method_dir = models_root / f"{selected_idx:02d}_{methods[selected_idx]['method']}"
    model_path = out_dir / "tabular-model.joblib"
    shutil.copy2(selected_method_dir / "tabular-model.joblib", model_path)
    selected_train_metrics_path = selected_method_dir / "training_metrics.json"
    if selected_train_metrics_path.is_file():
        shutil.copy2(selected_train_metrics_path, out_dir / "training_metrics.json")
    preprocessor_path = out_dir / "covariate-preprocessor.json"
    method_preproc_path = selected_method_dir / "covariate-preprocessor.json"
    if method_preproc_path.is_file():
        shutil.copy2(method_preproc_path, preprocessor_path)

    with open(selected_method_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        selected_meta = json.load(f)
    meta = {
        "model_backend": "tabular_sklearn",
        "model_type": selected_meta.get("method"),
        "selected_tabular_method": selected_meta.get("method"),
        "selected_tabular_method_index": int(selected_idx),
        "tabular_methods_evaluated": methods,
        "tabular_method_selection_metric": selection_metric,
        "tabular_method_selection_stat": selection_stat,
        "tabular_method_selection_score": float(best_score) if np.isfinite(best_score) else None,
        "tabular_method_model_dir": str(selected_method_dir),
        **selected_meta,
        "covariate_preprocessor_path": str(preprocessor_path) if method_preproc_path.is_file() else None,
    }
    with open(out_dir / "tabular-model-metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    selected_estimator = joblib.load(model_path)
    train_probs = np.asarray(selected_estimator.predict_proba(X), dtype=np.float64)
    train_pred = np.asarray(selected_estimator.predict(X), dtype=np.int32)
    comparison_dir = classifier_comparison_output_dir(project, out_dir)
    write_classification_results_csv(
        comparison_dir / CLASSIFICATION_RESULTS_FILENAME,
        sample_paths=all_paths,
        y_true=y_arr,
        y_pred=train_pred,
        probs=train_probs,
    )
    return model_path


def predict_tabular_model_from_project(
    project_json: str | Path,
    model_dir: str | Path,
    output_dir: str | Path,
    *,
    covariates_path: Optional[str] = None,
    covariate_id_column: str = "sample_id",
    covariates_strict_join: bool = False,
    covariates_missing_samples: Optional[str] = None,
    observed_feature_min_obs_fraction: Optional[float] = None,
    evaluation_partition: Optional[str] = None,
) -> Dict[str, Any]:
    model_dir = Path(model_dir).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    partition = str(evaluation_partition or "").strip().lower()

    model_path = model_dir / "tabular-model.joblib"
    meta_path = model_dir / "tabular-model-metadata.json"
    if not model_path.is_file() or not meta_path.is_file():
        raise FileNotFoundError(f"Tabular model artifacts not found under {model_dir}")

    estimator = joblib.load(model_path)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    class_names = [str(x) for x in meta.get("class_names", [])]
    feature_mode = str(meta.get("feature_mode", "raw_dmp")).strip().lower()
    selected_feature_names = [str(x) for x in (meta.get("selected_feature_names") or [])]
    with _project_cwd(project_json):
        project = load_project(project_json)
    class_centroid_dirs = _resolve_class_centroid_dirs(project, class_names)
    samples, y_true = _resolve_eval_paths_and_labels(
        project_json,
        class_names,
        evaluation_partition=partition or None,
    )
    sample_ids = sample_ids_from_paths(samples)
    dropped_eval_ids: List[str] = []
    cov_path = covariates_path or meta.get("covariates_path")
    if cov_path:
        kept_ids, dropped_eval_ids = resolve_covariate_sample_ids(
            sample_ids,
            cov_path,
            covariate_id_column or str(meta.get("covariate_id_column") or "sample_id"),
            missing_samples=covariates_missing_samples
            or meta.get("covariates_missing_samples"),
            strict_join=bool(covariates_strict_join or meta.get("covariates_strict_join", False)),
        )
        if dropped_eval_ids:
            keep = set(kept_ids)
            mask = [sid in keep for sid in sample_ids]
            samples = [path for path, keep_row in zip(samples, mask) if keep_row]
            y_true = [label for label, keep_row in zip(list(y_true), mask) if keep_row]
            sample_ids = list(kept_ids)

    obs_fraction_vec: Optional[np.ndarray] = None
    n_obs_dmps_vec: Optional[np.ndarray] = None
    n_total_dmps_vec: Optional[np.ndarray] = None
    if feature_mode == "observed_hybrid":
        bundle_h5 = meta.get("bundle_h5")
        if not isinstance(bundle_h5, str) or not Path(bundle_h5).is_file():
            raise FileNotFoundError("Observed-hybrid mode requires bundle_h5 in tabular metadata.")
        dmp_df = load_bundle_dmp_index(bundle_h5)
        max_dmps = int(meta.get("max_dmps", len(dmp_df) or 0))
        if max_dmps and len(dmp_df) > max_dmps:
            dmp_df = dmp_df.sort_values(["effect_size"], ascending=[False]).head(max_dmps).copy()
        feat = build_observed_hybrid_feature_table(
            samples,
            dmp_df,
            quantiles=meta.get("observed_feature_quantiles") or None,
            min_coverage=int(meta.get("observed_feature_min_coverage") or 1),
            include_dmp_features=bool(meta.get("observed_feature_include_dmp", True)),
            include_chromosome_features=bool(meta.get("observed_feature_include_chromosome", True)),
            include_dmr_features=bool(meta.get("observed_feature_include_dmr", True)),
            include_gene_features=bool(meta.get("observed_feature_include_gene", True)),
            dmr_window_bp=int(meta.get("observed_feature_dmr_window_bp", 100000)),
            max_dmr_features=int(meta.get("observed_feature_max_dmrs", 32)),
            max_gene_features=int(meta.get("observed_feature_max_genes", 32)),
            healthy_reference_vector=meta.get("observed_healthy_reference_vector"),
            cancer_reference_vector=meta.get("observed_cancer_reference_vector"),
            per_cancer_reference_vectors=meta.get("observed_per_cancer_reference_vectors"),
            healthy_class_label=meta.get("observed_healthy_class_label"),
            cancer_class_labels=meta.get("observed_cancer_class_labels") or [],
            all_class_labels=class_names,
            anchor_strategy=meta.get("observed_anchor_strategy"),
            expected_feature_order_fingerprint=meta.get("observed_feature_order_fingerprint"),
            centroid_dir_by_class_label=class_centroid_dirs,
            hist_eps=float(meta.get("observed_hist_eps", 1e-6)),
            hist_alpha=float(meta.get("observed_hist_alpha", 0.5)),
            hist_evidence_clip_cap=float(meta.get("observed_hist_evidence_clip_cap", 5.0)),
            hist_tail_agreement_threshold=float(
                meta.get("observed_hist_tail_agreement_threshold", 0.10)
            ),
            feature_family_set=coerce_saved_feature_family_set(str(meta.get("feature_family_set", "dmp_scored"))),
            gene_feature_loading=str(meta.get("gene_feature_loading", "frozen")),
            fixed_gene_features_df=load_bundle_gene_feature_ranges(bundle_h5),
            frozen_gene_panel_df=load_bundle_frozen_gene_panel(
                bundle_h5,
                project_json=project_json,
            ),
            gene_scored_min_support_n=int(meta.get("gene_scored_min_support_n", 2)),
            gene_scored_use_region_weight=bool(meta.get("gene_scored_use_region_weight", True)),
            gene_scored_gene_weight=str(
                meta.get("gene_scored_gene_weight", "importance_x_sqrt_support")
            ),
            gene_scored_ordered_comparison_labels=(
                meta.get("gene_scored_progression_order")
                or meta.get("gene_scored_ordered_comparison_labels")
            ),
            gene_scored_contrast_pairs=meta.get("gene_scored_contrast_pairs"),
            structural_scored_min_support_n=int(meta.get("structural_scored_min_support_n", 2)),
            structural_scored_use_region_weight=bool(meta.get("structural_scored_use_region_weight", True)),
            structural_scored_weight=str(meta.get("structural_scored_weight", "compound_x_sqrt_support")),
            structural_scored_ordered_comparison_labels=(
                meta.get("structural_scored_progression_order")
                or meta.get("structural_scored_ordered_comparison_labels")
            ),
            structural_scored_contrast_pairs=meta.get("structural_scored_contrast_pairs"),
            project_json=project_json,
            region_directional_region_types=meta.get("region_directional_region_types"),
            region_directional_min_loci=int(meta.get("region_directional_min_loci", 1)),
            observed_feature_quality_columns=meta.get("observed_feature_quality_columns"),
            **observed_chromosome_build_kwargs(
                chromosome_hypo_beta_threshold=meta.get("chromosome_hypo_beta_threshold"),
                chromosome_intermediate_beta_lo=meta.get("chromosome_intermediate_beta_lo"),
                chromosome_intermediate_beta_hi=meta.get("chromosome_intermediate_beta_hi"),
                chromosome_distance_metrics=meta.get("chromosome_distance_metrics"),
                chromosome_list=meta.get("chromosome_list"),
            ),
        )
        verify_feature_schema(
            feat.feature_names,
            meta.get("observed_feature_names") or [],
            context="tabular predict observed_hybrid export schema",
        )
        training_names = [str(x) for x in (meta.get("training_feature_names") or [])]
        if not training_names:
            quality_set = set(meta.get("quality_feature_names") or [])
            if not quality_set:
                quality_set = {"obs_fraction", "n_obs_dmps", "n_total_dmps"}
            training_names = [str(n) for n in feat.feature_names if str(n) not in quality_set]
        verify_feature_schema(
            training_names,
            meta.get("training_feature_names") or training_names,
            context="tabular predict observed_hybrid training schema",
        )
        X_full = np.asarray(feat.X, dtype=np.float32)
        if "obs_fraction" in feat.feature_names:
            obs_fraction_vec = X_full[:, feat.feature_names.index("obs_fraction")].astype(np.float32)
        if "n_obs_dmps" in feat.feature_names:
            n_obs_dmps_vec = X_full[:, feat.feature_names.index("n_obs_dmps")].astype(np.float32)
        if "n_total_dmps" in feat.feature_names:
            n_total_dmps_vec = X_full[:, feat.feature_names.index("n_total_dmps")].astype(np.float32)
        fill_values = meta.get("observed_feature_fill_values")
        if not isinstance(fill_values, list):
            raise ValueError("Observed-hybrid mode requires observed_feature_fill_values in metadata.")
        X_full = apply_feature_fill_values(X_full, fill_values)
        X = select_training_feature_matrix(X_full, feat.feature_names, training_names)
    else:
        feature_order = [
            (str(r["chromosome"]), str(r["context"]), int(r["position"]))
            for r in meta.get("feature_order", [])
        ]
        refs: Dict[str, Dict[str, np.ndarray]] = {}
        for chrom, ctx, pos in feature_order:
            refs.setdefault(chrom, {}).setdefault(ctx, []).append(int(pos))
        for chrom in list(refs.keys()):
            for ctx in list(refs[chrom].keys()):
                refs[chrom][ctx] = np.asarray(sorted(set(refs[chrom][ctx])), dtype=np.uint32)
        X = _extract_matrix_for_samples(samples, refs, feature_order, min_coverage=1)
        X = np.nan_to_num(np.asarray(X, dtype=np.float32), nan=0.5, posinf=0.5, neginf=0.5)
    preproc_path_meta = meta.get("covariate_preprocessor_path")
    preprocessor = (
        CovariatePreprocessor.load_json(preproc_path_meta)
        if isinstance(preproc_path_meta, str) and Path(preproc_path_meta).is_file()
        else None
    )
    cov, cov_report = transform_covariates(
        covariates_path or meta.get("covariates_path"),
        sample_ids,
        preprocessor,
        strict_join=True
        if dropped_eval_ids
        else bool(covariates_strict_join or meta.get("covariates_strict_join", False)),
    )
    cov_report = dict(cov_report or {"used": False})
    cov_report.update(exclusion_report(dropped_eval_ids))
    if cov is not None:
        X = np.concatenate([X, cov], axis=1)
    if selected_feature_names:
        raw_feature_names: List[str]
        if feature_mode == "observed_hybrid":
            raw_feature_names = list(training_names)
        else:
            raw_feature_names = [
                f"{str(r['chromosome'])}:{str(r['context'])}:{int(r['position'])}"
                for r in meta.get("feature_order", [])
            ]
        if cov is not None and preprocessor is not None:
            raw_feature_names = raw_feature_names + list(preprocessor.output_columns)
        idx_by_name = {str(name): i for i, name in enumerate(raw_feature_names)}
        keep_idx = [idx_by_name[name] for name in selected_feature_names if name in idx_by_name]
        if keep_idx:
            X = X[:, keep_idx]

    probs = estimator.predict_proba(X)
    y_pred = np.asarray(np.argmax(probs, axis=1), dtype=np.int32)
    eval_roles = meta.get("class_roles")
    if not isinstance(eval_roles, dict):
        eval_roles = resolve_class_roles(project)
    metrics = compute_validation_metrics(
        y_true,
        y_pred,
        class_names or ["control", "disease"],
        class_roles=eval_roles,
    )
    metrics["covariate_preprocessing"] = cov_report
    metrics["n_covariate_features_used"] = int(cov.shape[1]) if cov is not None else 0
    metrics["n_samples"] = int(len(samples))
    metrics["evaluation_partition"] = partition or "unspecified"
    if partition in {"train", "test"}:
        train_count = sum(
            len(paths or []) for _label, paths in project.get_resolved_groups()
        )
        metrics["n_train_samples"] = int(train_count)
        metrics["n_test_samples"] = int(len(samples)) if partition == "test" else None
        metrics["train_test_overlap_count"] = 0 if partition == "test" else None
        metrics["metrics_source"] = f"tabular_{partition}"
    if feature_mode == "observed_hybrid" and partition in {"", "test", "unspecified"}:
        active_family_set = coerce_saved_feature_family_set(str(meta.get("feature_family_set", "dmp_scored")))
        active_gene_loading = str(meta.get("gene_feature_loading", "frozen"))
        cm = metrics.get("confusion_matrix") or []
        worst_group_ba = None
        if isinstance(cm, list) and cm:
            recalls: List[float] = []
            for i, row in enumerate(cm):
                denom = float(np.sum(row))
                recalls.append(float(row[i]) / denom if denom > 0 else 0.0)
            if recalls:
                worst_group_ba = float(min(recalls))
        family_flags = describe_active_feature_families(active_family_set)
        ablation_report = {
            "backend": "tabular_sklearn",
            "feature_mode": feature_mode,
            "balanced_accuracy": metrics.get("balanced_accuracy"),
            "worst_group_balanced_accuracy": worst_group_ba,
            "robustness_score": (
                float(metrics.get("balanced_accuracy", 0.0)) - float(max(0.0, (1.0 - (worst_group_ba or 0.0))))
                if worst_group_ba is not None
                else None
            ),
            "active_feature_family_set": active_family_set,
            "active_gene_feature_loading": active_gene_loading,
            "active_feature_families": {
                **family_flags,
                "chromosome": bool(meta.get("observed_feature_include_chromosome", True)),
                "dmr": bool(meta.get("observed_feature_include_dmr", True)),
            },
            "mandatory_ablation_matrix": [
                {"name": "dmp_scored", "feature_family_set": "dmp_scored"},
                {"name": "dmp_scored+chromosome", "feature_family_set": "dmp_scored+chromosome"},
                {"name": "chromosome", "feature_family_set": "chromosome"},
                {"name": "gene_scored", "feature_family_set": "gene_scored"},
                {"name": "dmp_scored+gene_scored", "feature_family_set": "dmp_scored+gene_scored"},
                {"name": "structural_scored", "feature_family_set": "structural_scored"},
                {"name": "dmp_scored+structural_scored", "feature_family_set": "dmp_scored+structural_scored"},
            ],
        }
        with open(out_dir / "feature_family_ablation.json", "w", encoding="utf-8") as f:
            json.dump(ablation_report, f, indent=2)

    recs: List[Dict[str, Any]] = []
    min_obs = float(
        max(
            0.0,
            min(
                1.0,
                observed_feature_min_obs_fraction
                if observed_feature_min_obs_fraction is not None
                else float(meta.get("observed_feature_min_obs_fraction", 0.0)),
            ),
        )
    )
    for i, sample in enumerate(samples):
        rec: Dict[str, Any] = {
            "sample": Path(sample).name,
            "sample_path": sample,
            "expected_class": int(y_true[i]),
            "prediction": int(y_pred[i]),
        }
        for j in range(probs.shape[1]):
            rec[f"prob_class{j}"] = float(probs[i, j])
        if obs_fraction_vec is not None:
            obs_f = float(obs_fraction_vec[i])
            rec["obs_fraction"] = obs_f
            rec["low_evidence"] = bool(np.isfinite(obs_f) and obs_f < min_obs)
            rec["prediction_evidence_filtered"] = -1 if rec["low_evidence"] else int(y_pred[i])
        if n_obs_dmps_vec is not None:
            rec["n_obs_dmps"] = float(n_obs_dmps_vec[i])
        if n_total_dmps_vec is not None:
            rec["n_total_dmps"] = float(n_total_dmps_vec[i])
        recs.append(rec)
    pred_name = f"{partition}_predictions.csv" if partition in {"train", "test"} else "predictions.csv"
    pred_csv = out_dir / pred_name
    pd.DataFrame(recs).to_csv(pred_csv, index=False)
    metrics_name = f"{partition}_metrics.json" if partition in {"train", "test"} else "validation_metrics.json"
    metrics_path = out_dir / metrics_name
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    if partition == "test":
        shutil.copy2(pred_csv, out_dir / "predictions.csv")
        shutil.copy2(metrics_path, out_dir / "validation_metrics.json")
    return metrics

