"""
Hybrid generative backend for methyl-validation --model.

The model uses:
- detector-derived DMP feature weights from ModelFeatureBundle
- a linear latent encoder (VAE-style surrogate)
- class-conditional diagonal Gaussian density in latent space
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from methyl_predictor.project_resolver import resolve_predictor_config
from methyl_utils import load_project
from methyl_utils.methyl_centroid_pair import MethylCentroidPair

from .classification_metrics import (
    CLASSIFICATION_RESULTS_FILENAME,
    classifier_comparison_output_dir,
    compute_validation_metrics,
    resolve_class_roles,
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
from .model_bundle import (
    load_bundle_dmp_index,
    load_bundle_frozen_gene_panel,
    load_bundle_gene_feature_ranges,
    resolve_fixed_gene_features_panel,
)
from .model_datasets import (
    build_identity_feature_frame,
    read_dataset_frame,
    resolve_model_bundle_dir,
    test_dataset_path,
    train_dataset_path,
    write_dataset_frame,
    write_dataset_manifest,
    write_model_datasets,
)
from .observed_feature_builder import (
    apply_feature_fill_values,
    build_observed_hybrid_feature_table,
    coerce_saved_feature_family_set,
    derive_observed_hybrid_anchors,
    describe_active_feature_families,
    fit_feature_fill_values,
    normalize_feature_family_set,
    observed_chromosome_build_kwargs,
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


def _fit_linear_latent_encoder(X: np.ndarray, latent_dim: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    X_mean = np.mean(X, axis=0, dtype=np.float64).astype(np.float32)
    X_centered = X - X_mean
    _u, _s, vt = np.linalg.svd(X_centered, full_matrices=False)
    k = int(max(1, min(int(latent_dim), vt.shape[0])))
    components = vt[:k, :].astype(np.float32)
    z = X_centered @ components.T
    return X_mean, components, z


def _gaussian_log_prob_diag(z: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    var_safe = np.maximum(var, 1e-6)
    return -0.5 * np.sum(np.log(2.0 * np.pi * var_safe) + ((z - mean) ** 2) / var_safe, axis=1)


def _posterior_from_latent(
    z: np.ndarray,
    class_means: np.ndarray,
    class_vars: np.ndarray,
    class_priors: np.ndarray,
) -> np.ndarray:
    n_classes = class_means.shape[0]
    log_scores = np.zeros((z.shape[0], n_classes), dtype=np.float64)
    for cls in range(n_classes):
        log_scores[:, cls] = _gaussian_log_prob_diag(z, class_means[cls], class_vars[cls]) + np.log(
            max(float(class_priors[cls]), 1e-12)
        )
    log_scores -= np.max(log_scores, axis=1, keepdims=True)
    probs = np.exp(log_scores)
    denom = np.sum(probs, axis=1, keepdims=True)
    denom = np.where(denom <= 0.0, 1.0, denom)
    return (probs / denom).astype(np.float32)


def _resolve_eval_paths_and_labels(
    project_json: str | Path,
    class_names: List[str],
    *,
    evaluation_partition: Optional[str] = None,
) -> Tuple[List[str], Optional[np.ndarray]]:
    predictor_cfg = None
    partition = str(evaluation_partition or "").strip().lower()
    if partition not in {"train", "test"}:
        predictor_cfg = resolve_predictor_config(project_json)
    return resolve_eval_paths_and_labels(
        project_json,
        class_names,
        predictor_cfg=predictor_cfg,
        project_loader=load_project,
        evaluation_partition=partition or None,
    )


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


def train_generative_model(
    project_json: str | Path,
    bundle_h5: str | Path,
    output_dir: str | Path,
    *,
    max_dmps: Optional[int] = None,
    latent_dim: int = 16,
    kl_weight: float = 0.1,
    density_type: str = "diag_gaussian",
    epochs: int = 50,
    batch_size: int = 64,
    random_seed: int = 13,
    calibrate: bool = False,
    covariates_path: Optional[str] = None,
    covariate_id_column: str = "sample_id",
    covariates_strict_join: bool = True,
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
) -> Path:
    np.random.seed(int(random_seed))
    with _project_cwd(project_json):
        project = load_project(project_json)
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    dmp_df = load_bundle_dmp_index(bundle_h5)
    bundle_h5_path = Path(bundle_h5).expanduser().resolve()
    feature_family_set_norm = normalize_feature_family_set(feature_family_set)
    if family_includes_structural_scored(feature_family_set_norm):
        fixed_gene_features_df, _fixed_gene_features_path = resolve_fixed_gene_features_panel(
            project_json=project_json,
            bundle_dir=bundle_h5_path.parent,
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
                "feature_family_set includes gene_scored but frozen_genes_production.csv was not found."
            )
    if family_includes_structural_scored(feature_family_set):
        if fixed_gene_features_df.empty:
            raise FileNotFoundError(
                "feature_family_set includes structural_scored but frozen_gene_features.csv was not found."
            )
    max_dmps_norm = int(max_dmps) if (max_dmps is not None and int(max_dmps) > 0) else 0
    if max_dmps_norm and len(dmp_df) > max_dmps_norm:
        dmp_df = dmp_df.sort_values(["effect_size"], ascending=[False]).head(max_dmps_norm).copy()
    refs, feature_order = _build_reference_map(dmp_df)
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
    if len(all_paths) < 2:
        raise ValueError("Need at least 2 training samples to fit generative backend.")

    feature_mode_norm = str(feature_mode or "raw_dmp").strip().lower()
    gene_feature_loading_norm = str(gene_feature_loading or "frozen").strip().lower()
    if gene_feature_loading_norm not in {"frozen", "range"}:
        raise ValueError(
            f"gene_feature_loading must be one of ['frozen', 'range'], got {gene_feature_loading!r}"
        )
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
        X_obs_full = apply_feature_fill_values(X_obs_full, feature_fill_values.tolist())
        observed_feature_names = list(feat.feature_names)
        training_feature_names_obs = list(feat.training_feature_names)
        quality_feature_names = list(feat.quality_feature_names)
        X_methyl = select_training_feature_matrix(
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
        dmp_weights = np.ones((X_methyl.shape[1],), dtype=np.float32)
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
        gene_scored_progression_order = None
        structural_scored_progression_order = None
        X_methyl = _extract_matrix_for_samples(all_paths, refs, feature_order, min_coverage=1)
        X_methyl = np.nan_to_num(np.asarray(X_methyl, dtype=np.float32), nan=0.5, posinf=0.5, neginf=0.5)

        if "effect_size" not in dmp_df.columns:
            raise ValueError("Raw DMP feature mode requires effect_size in bundle DMP index.")
        dmp_weights = np.asarray(dmp_df["effect_size"].fillna(0.0).astype(np.float32).tolist(), dtype=np.float32)
        dmp_weights = np.abs(dmp_weights)
        if float(np.max(dmp_weights)) <= 0.0:
            dmp_weights = np.ones_like(dmp_weights, dtype=np.float32)
        else:
            dmp_weights = dmp_weights / float(np.max(dmp_weights))
        feature_fill_values = None
        observed_feature_names = []
        training_feature_names_obs = []
        quality_feature_names = []
        observed_feature_report = {}
        observed_feature_quantiles_out = [float(q) for q in (observed_feature_quantiles or [])]
        observed_healthy_reference = None
        observed_cancer_reference = None
        observed_per_cancer_references: List[np.ndarray] = []
        observed_healthy_class_index = None
        observed_healthy_class_label = None
        observed_cancer_class_labels = []
        observed_anchor_strategy = None
        observed_feature_order_fingerprint = None

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
    n_covariates = 0
    if cov is not None:
        n_covariates = int(cov.shape[1])
        X = np.concatenate([X_methyl, cov], axis=1)
        feature_weights = np.concatenate([dmp_weights, np.ones((n_covariates,), dtype=np.float32)], axis=0)
    else:
        X = X_methyl
        feature_weights = dmp_weights

    if feature_mode_norm == "observed_hybrid":
        # Match the training matrix (quality columns are excluded from X_methyl).
        base_feature_names = list(training_feature_names_obs)
    else:
        base_feature_names = [f"{c}:{ctx}:{int(pos)}" for c, ctx, pos in feature_order]
    cov_feature_names = list(preprocessor.output_columns) if preprocessor is not None else []
    feature_names = base_feature_names + cov_feature_names
    if len(feature_names) != int(X.shape[1]):
        raise ValueError(
            "Generative feature name schema mismatch: "
            f"names={len(feature_names)} matrix={int(X.shape[1])}"
        )
    y_arr = np.asarray(y, dtype=np.int32)

    X_weighted = X * feature_weights.reshape(1, -1)
    encoder_mean, encoder_components, z = _fit_linear_latent_encoder(X_weighted, latent_dim=latent_dim)

    n_classes = len(class_names)
    class_means = np.zeros((n_classes, z.shape[1]), dtype=np.float32)
    class_vars = np.zeros((n_classes, z.shape[1]), dtype=np.float32)
    class_priors = np.zeros((n_classes,), dtype=np.float32)
    for cls in range(n_classes):
        z_cls = z[y_arr == cls]
        if z_cls.shape[0] == 0:
            class_means[cls] = 0.0
            class_vars[cls] = 1.0
            class_priors[cls] = 1e-6
            continue
        class_means[cls] = np.mean(z_cls, axis=0, dtype=np.float64).astype(np.float32)
        class_vars[cls] = np.var(z_cls, axis=0, dtype=np.float64).astype(np.float32) + 1e-4
        class_priors[cls] = float(z_cls.shape[0]) / float(z.shape[0])

    train_probs = _posterior_from_latent(
        z=z,
        class_means=class_means,
        class_vars=class_vars,
        class_priors=class_priors,
    )
    y_pred_train = np.asarray(np.argmax(train_probs, axis=1), dtype=np.int32)
    train_metrics = compute_validation_metrics(
        y_arr, y_pred_train, class_names, class_roles=roles
    )
    train_metrics["metrics_source"] = "generative_train"
    train_metrics["evaluation_split"] = "training"
    train_metrics["n_train_samples"] = int(len(y_arr))
    train_metrics["n_train_features"] = int(X.shape[1])
    with open(out_dir / "training_metrics.json", "w", encoding="utf-8") as f:
        json.dump(train_metrics, f, indent=2)
    comparison_dir = classifier_comparison_output_dir(project, out_dir)
    write_classification_results_csv(
        comparison_dir / CLASSIFICATION_RESULTS_FILENAME,
        sample_paths=all_paths,
        y_true=y_arr,
        y_pred=y_pred_train,
        probs=train_probs,
    )

    model_path = out_dir / "generative-model.npz"
    np.savez_compressed(
        model_path,
        encoder_mean=encoder_mean.astype(np.float32),
        encoder_components=encoder_components.astype(np.float32),
        feature_weights=feature_weights.astype(np.float32),
        class_means=class_means.astype(np.float32),
        class_vars=class_vars.astype(np.float32),
        class_priors=class_priors.astype(np.float32),
    )

    preprocessor_path = out_dir / "covariate-preprocessor.json"
    if preprocessor is not None:
        preprocessor.save_json(preprocessor_path)

    meta = {
        "model_backend": "generative_hybrid",
        "architecture": "linear_encoder_diag_gaussian",
        "feature_mode": feature_mode_norm,
        "feature_family_set": feature_family_set_norm,
        "gene_feature_loading": gene_feature_loading_norm,
        "density_type": str(density_type),
        "class_names": class_names,
        "class_roles": roles,
        "n_classes": int(n_classes),
        "project_json": str(Path(project_json).resolve()),
        "bundle_h5": str(Path(bundle_h5).resolve()),
        "max_dmps": int(max_dmps_norm),
        "n_features": int(X.shape[1]),
        "n_dmps": int(len(feature_order)),
        "n_covariates": int(n_covariates),
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
        "selected_feature_names": list(feature_names),
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
        "covariate_preprocessor_path": str(preprocessor_path) if preprocessor is not None else None,
        "covariate_preprocessing": cov_report,
        "latent_dim_requested": int(latent_dim),
        "latent_dim_fitted": int(encoder_components.shape[0]),
        "kl_weight": float(kl_weight),
        "epochs": int(epochs),
        "batch_size": int(batch_size),
        "random_seed": int(random_seed),
        "calibrate": bool(calibrate),
    }
    with open(out_dir / "generative-model-metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    bundle_dir = resolve_model_bundle_dir(project_json=project_json)
    train_frame = build_identity_feature_frame(
        sample_ids=sample_ids,
        class_index=y_arr,
        class_names=class_names,
        feature_matrix=X,
        feature_names=feature_names,
    )
    write_model_datasets(
        bundle_dir,
        backend="generative_hybrid",
        train_frame=train_frame,
        test_frame=None,
        extra_manifest={
            "n_covariates": int(n_covariates),
            "feature_mode": feature_mode_norm,
        },
    )
    meta["train_dataset_path"] = str(train_dataset_path(bundle_dir))
    meta["model_bundle_dir"] = str(bundle_dir)
    with open(out_dir / "generative-model-metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return model_path


def predict_generative_model_from_project(
    project_json: str | Path,
    model_dir: str | Path,
    output_dir: str | Path,
    *,
    covariates_path: Optional[str] = None,
    covariate_id_column: str = "sample_id",
    covariates_strict_join: bool = True,
    covariates_missing_samples: Optional[str] = None,
    observed_feature_min_obs_fraction: Optional[float] = None,
    evaluation_partition: Optional[str] = None,
) -> Dict[str, Any]:
    model_dir = Path(model_dir).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    partition = str(evaluation_partition or "").strip().lower()

    model_path = model_dir / "generative-model.npz"
    meta_path = model_dir / "generative-model-metadata.json"
    if not model_path.is_file() or not meta_path.is_file():
        raise FileNotFoundError(f"Generative model artifacts not found under {model_dir}")

    data = np.load(model_path)
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
    if not samples:
        raise ValueError(
            "No evaluation samples resolved for generative prediction"
            f"{f' (partition={partition})' if partition else ''}."
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
    if feature_mode == "observed_hybrid":
        bundle_h5 = meta.get("bundle_h5")
        if not isinstance(bundle_h5, str) or not Path(bundle_h5).is_file():
            raise FileNotFoundError("Observed-hybrid mode requires bundle_h5 in generative metadata.")
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
            context="generative predict observed_hybrid export schema",
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
            context="generative predict observed_hybrid training schema",
        )
        X_full = np.asarray(feat.X, dtype=np.float32)
        if "obs_fraction" in feat.feature_names:
            obs_fraction_vec = X_full[:, feat.feature_names.index("obs_fraction")].astype(np.float32)
        fill_values = meta.get("observed_feature_fill_values")
        if not isinstance(fill_values, list):
            raise ValueError("Observed-hybrid mode requires observed_feature_fill_values in metadata.")
        X_full = apply_feature_fill_values(X_full, fill_values)
        X_methyl = select_training_feature_matrix(X_full, feat.feature_names, training_names)
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
        X_methyl = _extract_matrix_for_samples(samples, refs, feature_order, min_coverage=1)
        X_methyl = np.nan_to_num(np.asarray(X_methyl, dtype=np.float32), nan=0.5, posinf=0.5, neginf=0.5)

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
        X = np.concatenate([X_methyl, cov], axis=1)
    else:
        X = X_methyl
    if selected_feature_names:
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

    feature_weights = np.asarray(data["feature_weights"], dtype=np.float32)
    if X.shape[1] != feature_weights.shape[0]:
        raise ValueError(
            "Feature shape mismatch between prediction matrix and trained model: "
            f"{X.shape[1]} != {feature_weights.shape[0]}"
        )
    X_weighted = X * feature_weights.reshape(1, -1)
    encoder_mean = np.asarray(data["encoder_mean"], dtype=np.float32)
    encoder_components = np.asarray(data["encoder_components"], dtype=np.float32)
    z = (X_weighted - encoder_mean.reshape(1, -1)) @ encoder_components.T

    probs = _posterior_from_latent(
        z=z,
        class_means=np.asarray(data["class_means"], dtype=np.float32),
        class_vars=np.asarray(data["class_vars"], dtype=np.float32),
        class_priors=np.asarray(data["class_priors"], dtype=np.float32),
    )
    y_pred = np.asarray(np.argmax(probs, axis=1), dtype=np.int32)

    metrics: Dict[str, Any]
    if y_true is not None and y_true.shape[0] == y_pred.shape[0]:
        eval_roles = meta.get("class_roles")
        if not isinstance(eval_roles, dict):
            eval_roles = resolve_class_roles(project)
        metrics = compute_validation_metrics(
            y_true, y_pred, class_names, class_roles=eval_roles
        )
    else:
        metrics = {
            "n_samples": int(y_pred.shape[0]),
            "n_classes": int(len(class_names)),
            "class_names": class_names,
        }
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
        metrics["metrics_source"] = f"generative_{partition}"
    if feature_mode == "observed_hybrid" and partition in {"", "test"}:
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
            "backend": "generative_hybrid",
            "feature_mode": feature_mode,
            "balanced_accuracy": metrics.get("balanced_accuracy"),
            "worst_group_balanced_accuracy": worst_group_ba,
            "active_feature_family_set": active_family_set,
            "active_gene_feature_loading": active_gene_loading,
            "active_feature_families": family_flags,
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
            "prediction": int(y_pred[i]),
        }
        if y_true is not None and i < len(y_true):
            rec["expected_class"] = int(y_true[i])
        for j in range(probs.shape[1]):
            rec[f"prob_class{j}"] = float(probs[i, j])
        if obs_fraction_vec is not None:
            obs_f = float(obs_fraction_vec[i])
            rec["obs_fraction"] = obs_f
            rec["low_evidence"] = bool(np.isfinite(obs_f) and obs_f < min_obs)
            rec["prediction_evidence_filtered"] = -1 if rec["low_evidence"] else int(y_pred[i])
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
        try:
            bundle_dir = resolve_model_bundle_dir(
                meta.get("model_bundle_dir"),
                project_json=project_json,
            )
            feat_names = [str(x) for x in (meta.get("selected_feature_names") or [])]
            if feat_names and len(feat_names) == int(X.shape[1]) and y_true is not None:
                test_frame = build_identity_feature_frame(
                    sample_ids=sample_ids,
                    class_index=y_true,
                    class_names=class_names,
                    feature_matrix=X,
                    feature_names=feat_names,
                )
                train_path = train_dataset_path(bundle_dir)
                test_path = test_dataset_path(bundle_dir)
                write_dataset_frame(test_path, test_frame)
                overlap: List[str] = []
                if train_path.is_file():
                    train_ids = set(
                        read_dataset_frame(train_path)["sample_id"].astype(str)
                    )
                    test_ids = set(test_frame["sample_id"].astype(str))
                    overlap = sorted(train_ids & test_ids)
                    write_dataset_manifest(
                        bundle_dir,
                        backend="generative_hybrid",
                        train_path=train_path,
                        test_path=test_path,
                        feature_columns=feat_names,
                        train_test_overlap_count=int(len(overlap)),
                        overlapping_sample_ids=overlap,
                        extra={
                            "n_covariates": int(
                                cov.shape[1] if cov is not None else 0
                            ),
                            "feature_mode": feature_mode,
                        },
                    )
                metrics["test_dataset_path"] = str(test_path)
        except Exception as exc:
            metrics["test_dataset_export_error"] = str(exc)
    return metrics

