"""
Load and apply CLI overrides to MonteCarloConfig (shared by legacy and queue subcommands).
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from methyl_utils import load_project

from .cohort_inference import infer_monte_carlo_cohorts_from_project
from .config import MonteCarloConfig
def validate_stability_gene_biomarker_config(config: MonteCarloConfig) -> None:
    """Validate biomarker filter configuration."""
    if not bool(getattr(config, "stability_gene_biomarker_filter_enabled", False)):
        return
    if bool(getattr(config, "stability_gene_featurecuts_enabled", False)):
        return
    mode = str(getattr(config, "stability_gene_biomarker_mode", "ppi_only") or "ppi_only")
    if mode not in ("ppi_only", "disease_only", "disease_and_ppi"):
        print(
            f"Error: invalid stability_gene_biomarker_mode {mode!r}; "
            "expected ppi_only, disease_only, or disease_and_ppi.",
            file=sys.stderr,
        )
        sys.exit(1)


def _update_backend_params(config: MonteCarloConfig, backend: str, updates: dict[str, Any]) -> MonteCarloConfig:
    current = config.get_backend_params(backend)
    params_cls = type(current)
    merged = {**current.model_dump(mode="python"), **updates}
    params = params_cls.model_validate(merged)
    profiles = config.backend_profiles.model_copy(deep=True)
    if backend == "ecdf":
        profiles.ecdf = profiles.ecdf.model_copy(update={"params": params})
    elif backend == "tabular_sklearn":
        profiles.tabular_sklearn = profiles.tabular_sklearn.model_copy(update={"params": params})
    elif backend == "generative_hybrid":
        profiles.generative_hybrid = profiles.generative_hybrid.model_copy(update={"params": params})
    else:
        raise ValueError(f"Unsupported backend {backend!r}")
    return config.model_copy(update={"backend_profiles": profiles})


def apply_project_regulatory_to_mc_dict(
    mc_config_dict: dict[str, Any],
    project: Any,
) -> dict[str, Any]:
    """Copy study-owned lifecycle contracts into MonteCarloConfig."""
    merged = dict(mc_config_dict)
    get_regulatory = getattr(project, "get_regulatory_config", None)
    if callable(get_regulatory):
        regulatory = get_regulatory()
        if regulatory:
            merged["regulatory"] = regulatory

    partitions = getattr(project, "validation_partitions", None)
    if partitions:
        merged["validation_partitions"] = partitions
    return merged


def load_monte_carlo_config(
    args: Namespace,
    parser: Union[ArgumentParser, None] = None,
) -> Tuple[MonteCarloConfig, bool]:
    """
    Load config from --project (profile actionConfig.validation) or --config.

    Returns (config, project_mode) where project_mode is True if loaded from --project.
    Exits the process on error if parser is not None, else may raise.
    """
    def _err(msg: str) -> None:
        print(msg, file=sys.stderr)
        if parser is not None:
            sys.exit(1)
        raise ValueError(msg)

    if args.project is not None:
        from methyl_utils.action_config_resolver import resolve_for_project

        with open(args.project, encoding="utf-8") as f:
            project_data = json.load(f)

        if "step_config" in project_data:
            _err(
                f"Error: Project {args.project} still contains step_config; "
                "run scripts/migrate_project_config.py and set METHYL_PROFILE."
            )

        project = load_project(str(args.project))
        validation_settings = dict(resolve_for_project("validation", project))
        if not validation_settings:
            _err(
                f"Error: Project {args.project} missing resolved validation action config "
                "(set METHYL_PROFILE or pass --config)."
            )
        cohorts = infer_monte_carlo_cohorts_from_project(project_data, args.project)
        if len(cohorts) < 2:
            _err(
                "Error: Could not infer >=2 Monte Carlo cohorts from project. "
                "Define project controls/diseases sample_paths (or flat groups) with CSVs."
            )

        mc_config_dict = apply_project_regulatory_to_mc_dict(
            {
                "samples_base_path": project_data.get("samples_base_path", "/work/samples"),
                "base_project": str(args.project),
                "output_base": project_data.get("output_base", "/work/projects/prostate-cancer"),
                "path_remap": project_data.get("path_remap"),
                "cohorts": cohorts,
                **validation_settings,
            },
            project,
        )
        return MonteCarloConfig.model_validate(mc_config_dict), True
    if args.config is not None:
        return MonteCarloConfig.from_json_file(args.config), False
    if parser is not None:
        parser.error("Either --config or --project must be provided")
    raise ValueError("Either --config or --project must be provided")


def apply_monte_carlo_config_overrides(
    config: MonteCarloConfig,
    args: Any,
) -> MonteCarloConfig:
    """
    Apply argparse-style overrides to config (shared fields with legacy main).
    `args` must expose the same optional attributes as the legacy parser.
    """
    if getattr(args, "iterations", None) is not None:
        config = config.model_copy(update={"n_iterations": int(args.iterations)})
    if getattr(args, "seed", None) is not None:
        config = config.model_copy(update={"seed": int(args.seed)})
    if getattr(args, "output_base", None) is not None:
        config = config.model_copy(update={"output_base": str(args.output_base)})
    if getattr(args, "samples_base_path", None) is not None:
        config = config.model_copy(update={"samples_base_path": str(args.samples_base_path)})
    if getattr(args, "path_remap", None):
        merged = dict(config.path_remap or {})
        for item in args.path_remap:
            if "=" not in item:
                print(
                    f"Error: --path-remap must be OLD=NEW, got: {item!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
            old_p, new_p = item.split("=", 1)
            if not old_p.strip():
                print(f"Error: empty OLD prefix in --path-remap: {item!r}", file=sys.stderr)
                sys.exit(1)
            merged[old_p] = new_p
        config = config.model_copy(update={"path_remap": merged})
    if getattr(args, "stability", None):
        config = config.model_copy(update={"run_stability": True})
    if getattr(args, "stability_featurecuts", None):
        config = config.model_copy(update={"stability_featurecuts_enabled": True})
    if getattr(args, "stability_target_ba", None) is not None:
        config = config.model_copy(
            update={"stability_target_balanced_accuracy": float(args.stability_target_ba)}
        )
    if getattr(args, "stability_min_selected_dmps", None) is not None:
        # Deprecated CLI alias; canonical field is stability_min_core_dmps.
        config = config.model_copy(
            update={"stability_min_core_dmps": int(args.stability_min_selected_dmps)}
        )
    if getattr(args, "stability_min_core_dmps", None) is not None:
        config = config.model_copy(
            update={"stability_min_core_dmps": int(args.stability_min_core_dmps)}
        )
    if getattr(args, "stability_classifier_export_margin_pct", None) is not None:
        config = config.model_copy(
            update={
                "stability_classifier_export_margin_pct": float(
                    args.stability_classifier_export_margin_pct
                )
            }
        )
    if getattr(args, "stability_classifier_export_margin_abs", None) is not None:
        config = config.model_copy(
            update={
                "stability_classifier_export_margin_abs": int(
                    args.stability_classifier_export_margin_abs
                )
            }
        )
    if getattr(args, "stability_classifier_export_max_dmps", None) is not None:
        config = config.model_copy(
            update={
                "stability_classifier_export_max_dmps": int(
                    args.stability_classifier_export_max_dmps
                )
            }
        )
    if getattr(args, "stability_gene_featurecuts", None):
        config = config.model_copy(update={"stability_gene_featurecuts_enabled": True})
    if getattr(args, "stability_gene_biomarker_filter", None):
        config = config.model_copy(update={"stability_gene_biomarker_filter_enabled": True})
    if getattr(args, "stability_gene_biomarker_mode", None) is not None:
        config = config.model_copy(
            update={"stability_gene_biomarker_mode": str(args.stability_gene_biomarker_mode)}
        )
    if getattr(args, "stability_gene_region_hits", None):
        config = config.model_copy(
            update={"stability_gene_region_hits": list(args.stability_gene_region_hits)}
        )
    if getattr(args, "stability_min_selected_genes", None) is not None:
        config = config.model_copy(
            update={"stability_min_selected_genes": int(args.stability_min_selected_genes)}
        )
    if getattr(args, "skip_enricher", None):
        print(
            "Error: --skip-enricher was removed. Control enricher via DomainProgram "
            "topology or actionConfig.enricher.skip.",
            file=sys.stderr,
        )
        sys.exit(1)
    if getattr(args, "predictor_only", None):
        config = config.model_copy(update={"predictor_only": True})
    if getattr(args, "holdout_eval", None):
        config = config.model_copy(update={"holdout_eval": True})
    if getattr(args, "holdout_partition", None):
        config = config.model_copy(update={"holdout_partition": str(args.holdout_partition)})
    if getattr(args, "holdout_n_bootstrap", None) is not None:
        config = config.model_copy(update={"holdout_n_bootstrap": int(args.holdout_n_bootstrap)})
    if getattr(args, "holdout_ci", None) is not None:
        config = config.model_copy(update={"holdout_ci": float(args.holdout_ci)})
    if getattr(args, "holdout_seed", None) is not None:
        config = config.model_copy(update={"holdout_seed": int(args.holdout_seed)})

    validate_stability_gene_biomarker_config(config)

    if getattr(args, "model_backend", None) and getattr(args, "post_model_backend", None):
        if args.model_backend != args.post_model_backend:
            print(
                "Error: --model-backend and --post-model-backend must match when both are provided.",
                file=sys.stderr,
            )
            sys.exit(1)
    selected_backend = getattr(args, "post_model_backend", None) or getattr(args, "model_backend", None)
    if selected_backend:
        config = config.with_backend_selection(str(selected_backend))

    if getattr(args, "covariates_path", None) is not None:
        for backend in ("ecdf", "tabular_sklearn", "generative_hybrid"):
            config = _update_backend_params(config, backend, {"covariates_path": str(args.covariates_path)})
    if getattr(args, "tabular_max_dmps", None) is not None:
        for backend in ("ecdf", "tabular_sklearn", "generative_hybrid"):
            config = _update_backend_params(config, backend, {"tabular_max_dmps": int(args.tabular_max_dmps)})
    if getattr(args, "tabular_model_type", None):
        mt = str(args.tabular_model_type)
        config = _update_backend_params(
            config,
            "tabular_sklearn",
            {"tabular_model_type": mt, "tabular_methods": [{"method": mt, "params": {}}]},
        )
    if getattr(args, "tabular_methods_json", None):
        try:
            parsed_methods = json.loads(str(args.tabular_methods_json))
        except Exception as e:
            print(f"Error: invalid --tabular-methods-json: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(parsed_methods, list) or not parsed_methods:
            print("Error: --tabular-methods-json must be a non-empty JSON array.", file=sys.stderr)
            sys.exit(1)
        config = _update_backend_params(config, "tabular_sklearn", {"tabular_methods": parsed_methods})
    if getattr(args, "tabular_save_train_dataset", None):
        config = _update_backend_params(config, "tabular_sklearn", {"tabular_save_train_dataset": True})
    if getattr(args, "no_tabular_reuse_train_dataset", None):
        config = _update_backend_params(config, "tabular_sklearn", {"tabular_reuse_train_dataset": False})
    if getattr(args, "tabular_train_dataset_path", None) is not None:
        config = _update_backend_params(
            config,
            "tabular_sklearn",
            {"tabular_train_dataset_path": str(args.tabular_train_dataset_path)},
        )
    if getattr(args, "no_tabular_save_test_dataset", None):
        config = _update_backend_params(config, "tabular_sklearn", {"tabular_save_test_dataset": False})
    if getattr(args, "tabular_test_dataset_path", None) is not None:
        config = _update_backend_params(
            config,
            "tabular_sklearn",
            {"tabular_test_dataset_path": str(args.tabular_test_dataset_path)},
        )
    if getattr(args, "generative_latent_dim", None) is not None:
        config = _update_backend_params(
            config, "generative_hybrid", {"generative_latent_dim": int(args.generative_latent_dim)}
        )
    if getattr(args, "generative_kl_weight", None) is not None:
        config = _update_backend_params(
            config, "generative_hybrid", {"generative_kl_weight": float(args.generative_kl_weight)}
        )
    if getattr(args, "generative_density_type", None):
        config = _update_backend_params(
            config, "generative_hybrid", {"generative_density_type": str(args.generative_density_type)}
        )
    if getattr(args, "generative_epochs", None) is not None:
        config = _update_backend_params(
            config, "generative_hybrid", {"generative_epochs": int(args.generative_epochs)}
        )
    if getattr(args, "generative_batch_size", None) is not None:
        config = _update_backend_params(
            config, "generative_hybrid", {"generative_batch_size": int(args.generative_batch_size)}
        )
    if getattr(args, "generative_seed", None) is not None:
        config = _update_backend_params(
            config, "generative_hybrid", {"generative_seed": int(args.generative_seed)}
        )
    if getattr(args, "generative_calibrate", None):
        config = _update_backend_params(config, "generative_hybrid", {"generative_calibrate": True})
    if getattr(args, "no_generative_covariates_strict", None):
        config = _update_backend_params(
            config, "generative_hybrid", {"generative_covariates_strict": False}
        )

    return config


def ensure_monte_carlo_output_tree(
    config: MonteCarloConfig,
) -> Tuple[Path, Any, Path, Path]:
    """
    Returns (base_project, base_project_config, output_base, monte_carlo_runs_root) after mkdir.
    """
    base_project = Path(config.base_project)
    if not base_project.is_file():
        print(f"Error: base_project not found: {base_project}", file=sys.stderr)
        sys.exit(1)

    base_project_config = load_project(config.base_project)
    output_base = Path(config.output_base)
    output_base.mkdir(parents=True, exist_ok=True)
    project_name = base_project_config.project_name
    monte_carlo_runs_root = output_base / project_name / "monte_carlo_runs"
    monte_carlo_runs_root.mkdir(parents=True, exist_ok=True)
    return base_project, base_project_config, output_base, monte_carlo_runs_root


def write_mc_config_snapshot(
    config: MonteCarloConfig,
    path: Path,
) -> None:
    """Write canonical MonteCarloConfig and informational effective snapshot for operators."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(config.dump_clean_json(indent=2))
    from .effective_config import write_effective_mc_config_snapshot

    write_effective_mc_config_snapshot(config, path.with_name("mc_config.effective.json"))
