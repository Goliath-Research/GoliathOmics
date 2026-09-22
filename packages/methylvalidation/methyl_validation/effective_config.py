"""
Activation-aware effective Monte Carlo configuration for operators.

The canonical ``mc_config.json`` remains the complete resolved machine snapshot.
``mc_config.effective.json`` nests dependent parameters under their enabling
feature switches so inactive knobs are not shown as if they apply.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import MonteCarloConfig


def _enabled_section(enabled: bool, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"enabled": bool(enabled)}
    if enabled and params:
        out.update(params)
    return out


def _project_early_stopping(config: MonteCarloConfig) -> Dict[str, Any]:
    return _enabled_section(
        config.stability_early_stop_enabled,
        {
            "minimum_qualifying_iterations": config.stability_min_iterations,
            "comparison_window": config.stability_convergence_window,
            "minimum_jaccard_similarity": config.stability_convergence_jaccard,
            "maximum_relative_size_change": config.stability_convergence_max_size_delta,
            "required_consecutive_passes": config.stability_convergence_patience,
        },
    )


def _project_dual_cutoffs(config: MonteCarloConfig) -> Dict[str, Any]:
    return _enabled_section(
        config.stability_dual_cutoff_enabled,
        {
            "relaxed_cutoff_mode": config.stability_relaxed_cutoff_mode,
            "relaxed_multiplier": config.stability_relaxed_multiplier,
            "score_eps": config.stability_score_eps,
        },
    )


def _project_stability_tiers(config: MonteCarloConfig) -> Dict[str, Any]:
    return _enabled_section(
        config.stability_tiers_enabled,
        {
            "core_freq": config.stability_tier_core_freq,
            "extended_freq": config.stability_tier_extended_freq,
            "exploratory_freq": config.stability_tier_exploratory_freq,
            "default_freeze_tier": config.stability_default_freeze_tier,
        },
    )


def _project_biomarker_filter(config: MonteCarloConfig) -> Dict[str, Any]:
    return _enabled_section(
        bool(config.stability_gene_biomarker_filter_enabled),
        {
            "mode": config.stability_gene_biomarker_mode,
            "region_hits": list(config.stability_gene_region_hits or []),
            "top_genes": config.stability_gene_biomarker_top_genes,
            "ppi_top_hubs": config.stability_gene_biomarker_ppi_top_hubs,
            "min_degree": config.stability_gene_biomarker_min_degree,
            "ppi_cache_path": config.stability_gene_biomarker_ppi_cache_path,
        },
    )


def _project_holdout(config: MonteCarloConfig) -> Dict[str, Any]:
    return _enabled_section(
        bool(config.holdout_eval),
        {
            "partition": config.holdout_partition,
            "n_bootstrap": config.holdout_n_bootstrap,
            "ci": config.holdout_ci,
            "seed": config.holdout_seed,
            "stratified": config.holdout_stratified,
            "exclude_from_training": config.holdout_exclude_from_training,
        },
    )


def _project_freeze_stage(config: MonteCarloConfig) -> Dict[str, Any]:
    has_freeze = bool(
        config.freeze_stable_dmp_csv
        or config.freeze_stable_gene_csv
        or config.production_output_dir
        or config.frozen_project_path
    )
    return _enabled_section(
        has_freeze,
        {
            "stable_dmp_csv": config.freeze_stable_dmp_csv,
            "stable_gene_csv": config.freeze_stable_gene_csv,
            "min_dmps_per_feature": config.freeze_min_dmps_per_feature,
            "gene_importance_min": config.freeze_gene_importance_min,
            "top_genes": config.freeze_top_genes,
            "production_output_dir": config.production_output_dir,
            "frozen_project_path": config.frozen_project_path,
        },
    )


def _project_model_stage(config: MonteCarloConfig) -> Dict[str, Any]:
    return _enabled_section(
        bool(config.predictor_only),
        {
            "predictor_only": True,
            "frozen_project_path": config.frozen_project_path,
            "model_backend": config.model_backend,
        },
    )


def _covariate_block(params: Any) -> Optional[Dict[str, Any]]:
    path = getattr(params, "covariates_path", None)
    if not path:
        return None
    return {
        "path": path,
        "id_column": getattr(params, "covariate_id_column", "sample_id"),
        "numeric_columns": getattr(params, "covariate_numeric_columns", None),
        "ordinal_columns": getattr(params, "covariate_ordinal_columns", None),
        "ordinal_maps": getattr(params, "covariate_ordinal_maps", None),
        "ordinal_unknown_value": getattr(params, "covariate_ordinal_unknown_value", 0.0),
        "categorical_columns": getattr(params, "covariate_categorical_columns", None),
        "missing_numeric_strategy": getattr(params, "covariate_missing_numeric_strategy", "mean"),
        "standardize_numeric": getattr(params, "covariate_standardize_numeric", True),
        "strict_join": getattr(params, "covariates_strict_join", False),
        "missing_samples": getattr(params, "covariates_missing_samples", None),
    }


def _project_ecdf_backend(config: MonteCarloConfig) -> Dict[str, Any]:
    profile = config.backend_profiles.ecdf
    if not profile.enabled:
        return {"enabled": False}
    params = profile.params
    feature_mode = str(params.feature_mode or "raw_dmp").strip().lower()
    covariates = _covariate_block(params)
    # Sole gate is ecdf_second_stage_enabled (covariates_path coerces it true at validate).
    second_stage_active = bool(params.ecdf_second_stage_enabled)
    hybrid_requested = bool(
        getattr(params, "ecdf_second_stage_include_observed_hybrid", False)
    )
    # Effective hybrid inclusion requires the stacker to actually run.
    include_observed_hybrid = bool(second_stage_active and hybrid_requested)
    out: Dict[str, Any] = {
        "enabled": True,
        "feature_mode": feature_mode,
        "feature_family_set": params.feature_family_set,
        "second_stage_active": second_stage_active,
        "include_covariates": bool(covariates),
        "include_observed_hybrid": include_observed_hybrid,
        "ecdf_second_stage_enabled": params.ecdf_second_stage_enabled,
        "ecdf_second_stage_include_observed_hybrid": hybrid_requested,
    }
    if feature_mode == "raw_gene":
        out["gene_feature_loading"] = params.gene_feature_loading
        out["mapper_gene_columns"] = list(params.mapper_gene_columns or [])
    if feature_mode == "observed_hybrid" or include_observed_hybrid:
        out["observed_feature_quantiles"] = list(params.observed_feature_quantiles or [])
        out["observed_feature_min_coverage"] = params.observed_feature_min_coverage
        out["observed_feature_min_obs_fraction"] = params.observed_feature_min_obs_fraction
        out["observed_feature_include_dmp"] = params.observed_feature_include_dmp
        out["observed_feature_include_chromosome"] = params.observed_feature_include_chromosome
        out["observed_feature_include_dmr"] = params.observed_feature_include_dmr
        out["observed_feature_include_gene"] = params.observed_feature_include_gene
    if bool(params.ecdf_aggregated_enabled):
        out["ecdf_aggregated"] = {
            "enabled": True,
            "n_bins": params.ecdf_aggregated_n_bins,
        }
    else:
        out["ecdf_aggregated"] = {"enabled": False}
    if covariates:
        out["covariates"] = covariates
    return out


def _project_tabular_backend(config: MonteCarloConfig) -> Dict[str, Any]:
    profile = config.backend_profiles.tabular_sklearn
    if not profile.enabled:
        return {"enabled": False}
    params = profile.params
    out: Dict[str, Any] = {
        "enabled": True,
        "feature_mode": params.feature_mode,
        "feature_family_set": params.feature_family_set,
        "tabular_methods": [m.model_dump(mode="json") for m in params.tabular_methods],
        "tabular_method_selection_metric": params.tabular_method_selection_metric,
        "tabular_method_selection_stat": params.tabular_method_selection_stat,
        "tabular_save_train_dataset": params.tabular_save_train_dataset,
        "tabular_reuse_train_dataset": params.tabular_reuse_train_dataset,
        "tabular_train_dataset_path": params.tabular_train_dataset_path,
        "tabular_save_test_dataset": params.tabular_save_test_dataset,
        "tabular_test_dataset_path": params.tabular_test_dataset_path,
    }
    covariates = _covariate_block(params)
    if covariates:
        out["covariates"] = covariates
    return out


def _project_generative_backend(config: MonteCarloConfig) -> Dict[str, Any]:
    profile = config.backend_profiles.generative_hybrid
    if not profile.enabled:
        return {"enabled": False}
    params = profile.params
    out: Dict[str, Any] = {
        "enabled": True,
        "feature_mode": params.feature_mode,
        "feature_family_set": params.feature_family_set,
        "latent_dim": params.generative_latent_dim,
        "kl_weight": params.generative_kl_weight,
        "density_type": params.generative_density_type,
        "epochs": params.generative_epochs,
        "batch_size": params.generative_batch_size,
        "seed": params.generative_seed,
        "calibrate": params.generative_calibrate,
        "covariates_strict": params.generative_covariates_strict,
    }
    covariates = _covariate_block(params)
    if covariates:
        out["covariates"] = covariates
    return out


def build_effective_mc_config(config: MonteCarloConfig) -> Dict[str, Any]:
    """Project a nested, activation-aware view of ``MonteCarloConfig`` for operators."""
    stability_consumed = {
        "run_stability": config.run_stability,
        "dmp_freq": config.stability_dmp_freq,
        "gene_freq": config.stability_gene_freq,
        "min_balanced_accuracy": config.stability_min_balanced_accuracy,
        "dmp_modeling_mode": config.dmp_modeling_mode,
        "gene_modeling_mode": config.gene_modeling_mode,
        "featurecuts": _enabled_section(
            bool(config.stability_featurecuts_enabled),
            {
                "target_balanced_accuracy": config.stability_target_balanced_accuracy
                or config.dmp_featurecuts_target_ba,
                "min_core_dmps": config.stability_min_core_dmps or config.dmp_featurecuts_min_dmps,
                "max_dmps": config.dmp_featurecuts_max_dmps
                or config.stability_classifier_export_max_dmps,
                "export_margin_pct": config.stability_classifier_export_margin_pct,
                "export_margin_abs": config.stability_classifier_export_margin_abs,
            },
        ),
        "gene_featurecuts": _enabled_section(
            bool(config.stability_gene_featurecuts_enabled),
            {
                "target_balanced_accuracy": config.gene_featurecuts_target_ba,
                "min_selected_genes": config.stability_min_selected_genes,
                "max_dmps": config.stability_gene_featurecuts_max_dmps,
                "max_genes": config.stability_gene_featurecuts_max_genes,
                "loci_source": config.gene_featurecuts_loci_source,
            },
        ),
        "early_stopping": _project_early_stopping(config),
        "dual_cutoffs": _project_dual_cutoffs(config),
        "tiers": _project_stability_tiers(config),
        "biomarker_filter": _project_biomarker_filter(config),
    }

    model_training_carried = {
        "note": (
            "Settings below are consumed by freeze/model stages and backend training; "
            "mc_stability.program.json iterations use the stability section above."
        ),
        "selected_backend": config.model_backend,
        "backends": {
            "ecdf": _project_ecdf_backend(config),
            "tabular_sklearn": _project_tabular_backend(config),
            "generative_hybrid": _project_generative_backend(config),
        },
        "freeze": _project_freeze_stage(config),
        "model_stage": _project_model_stage(config),
        "holdout_evaluation": _project_holdout(config),
    }

    return {
        "schema": "mc_config.effective.v1",
        "informational": True,
        "note": (
            "Informational projection of the resolved Monte Carlo snapshot. "
            "Edit site/profile/context layers; do not treat this file as the editable source."
        ),
        "run": {
            "samples_base_path": config.samples_base_path,
            "base_project": config.base_project,
            "output_base": config.output_base,
            "cohorts": [c.model_dump(mode="json") for c in config.cohorts],
            "train_fraction": config.train_fraction,
            "n_iterations": config.n_iterations,
            "seed": config.seed,
            "path_remap": config.path_remap,
            "abort_on_step_failure": config.abort_on_step_failure,
            "parallel_mc_centroid_seed": config.parallel_mc_centroid_seed,
        },
        "stability": stability_consumed,
        "model_training": model_training_carried,
        "regulatory": config.regulatory.model_dump(mode="json", exclude_none=True)
        if hasattr(config.regulatory, "model_dump")
        else config.regulatory,
    }


def write_effective_mc_config_snapshot(config: MonteCarloConfig, path: Path) -> Path:
    """Write ``mc_config.effective.json`` beside the canonical snapshot."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_effective_mc_config(config)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
