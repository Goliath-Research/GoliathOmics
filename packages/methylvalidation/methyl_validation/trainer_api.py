"""
Backend training API boundary for ``methyl-validation --model``.

This module isolates backend-specific step construction so orchestration can
stay in MethylValidation now and be extracted to a dedicated trainer package
later with minimal CLI/workflow changes.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

StepFn = Callable[[], tuple[int, str, str]]
TrainerStep = Tuple[str, StepFn]


def _normalized_feature_family_set(config: Any) -> str:
    from .observed_feature_builder import normalize_feature_family_set

    raw = config.feature_family_set if config is not None else "dmp_scored"
    return normalize_feature_family_set(raw)


def _requires_mapper_annotations(feature_family_set: str) -> bool:
    return str(feature_family_set) != "dmp_scored"


def _write_ecdf_training_metrics(project_json: Path, classifier_output_dir: Path) -> tuple[bool, str]:
    """
    Evaluate the ECDF classifier on training cohorts and persist training_metrics.json.

    Returns (success, message_or_path).
    """
    try:
        from methyl_predictor.core.predictor import run_prediction
        from methyl_predictor.models.config import PredictorConfig
        from methyl_predictor.project_resolver import resolve_predictor_config
        from methyl_utils import load_project
    except Exception as e:
        return False, f"ECDF training metrics unavailable (imports): {e}"

    try:
        project = load_project(project_json)
        resolved_groups = project.get_resolved_groups()
        if not resolved_groups:
            return False, "ECDF training metrics unavailable: no resolved training groups."

        base_predictor = resolve_predictor_config(project_json)
        test_group_paths = [
            {
                "label": str(label),
                "class_index": int(idx),
                "paths": [str(p) for p in (paths or [])],
            }
            for idx, (label, paths) in enumerate(resolved_groups)
            if paths
        ]
        if not test_group_paths:
            return False, "ECDF training metrics unavailable: resolved groups had no sample paths."

        classifier_output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=str(classifier_output_dir),
            prefix=".ecdf-training-eval-",
        ) as tmp_out:
            cfg = PredictorConfig(
                model_path=base_predictor.model_path,
                model_dir=base_predictor.model_dir,
                output_dir=tmp_out,
                test_group_paths=test_group_paths,
                test_control_paths=[],
                test_disease_paths=[],
                samples_base_path=base_predictor.samples_base_path,
                path_remap=base_predictor.path_remap,
                debug=bool(base_predictor.debug),
                classifier_step_snapshot=base_predictor.classifier_step_snapshot,
                panel=base_predictor.panel,
                decision_enabled=bool(base_predictor.decision_enabled),
                decision_min_margin=float(base_predictor.decision_min_margin),
                decision_min_confidence=float(base_predictor.decision_min_confidence),
            )
            metrics = run_prediction(cfg)

        if not isinstance(metrics, dict) or not metrics:
            return False, "ECDF training metrics unavailable: predictor returned empty metrics."

        payload = dict(metrics)
        payload["metrics_source"] = "ecdf_train"
        payload["evaluation_split"] = "training"
        payload["model_backend"] = "ecdf"
        n_train_samples = 0
        for g in test_group_paths:
            paths = g.get("paths")
            if isinstance(paths, list):
                n_train_samples += len(paths)
        payload["n_train_samples"] = int(n_train_samples)
        payload["n_train_groups"] = int(len(test_group_paths))
        out_path = classifier_output_dir / "train_metrics.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        shutil.copy2(out_path, classifier_output_dir / "training_metrics.json")
        return True, str(out_path)
    except Exception as e:
        return False, f"ECDF training metrics unavailable: {e}"


def _classifier_output_dir(project_json: Path, predictor_output_dir: Optional[Path]) -> Path:
    del predictor_output_dir  # predictor and classifier roots both derive from project_json.parent
    return project_json.parent / "classifiers"


def _load_binary_partition_paths(project_json: Path, partition: str) -> tuple[List[str], List[str]]:
    """Load control/disease sample paths for train or test from MC sidecar CSVs."""
    from .split import load_and_resolve_sample_paths

    run_dir = Path(project_json).resolve().parent
    if partition == "train":
        control_csv = run_dir / "train_control.csv"
        disease_csv = run_dir / "train_disease.csv"
    elif partition == "test":
        control_csv = run_dir / "test_control.csv"
        if not control_csv.is_file():
            control_csv = run_dir / "val_control.csv"
        disease_csv = run_dir / "test_disease.csv"
        if not disease_csv.is_file():
            disease_csv = run_dir / "val_disease.csv"
    else:
        raise ValueError(f"Unsupported partition {partition!r}")
    if not control_csv.is_file() or not disease_csv.is_file():
        raise FileNotFoundError(
            f"Missing {partition} sidecars under {run_dir}: "
            f"{control_csv.name}, {disease_csv.name}"
        )
    samples_base = ""
    try:
        payload = json.loads(Path(project_json).read_text(encoding="utf-8"))
        samples_base = str(payload.get("samples_base_path") or "")
    except (OSError, json.JSONDecodeError, AttributeError):
        samples_base = ""
    return (
        load_and_resolve_sample_paths(control_csv, samples_base),
        load_and_resolve_sample_paths(disease_csv, samples_base),
    )


def _resolve_classic_ecdf_model_locations(
    project_json: Path,
) -> tuple[Optional[str], Optional[str], Any]:
    """
    Resolve model_path/model_dir the same way ``methyl-predictor --project`` does
    for control/disease comparisons (comparison-scoped detection dir, not top-level
    ``detections/``).
    """
    from methyl_predictor.project_resolver import (
        resolve_predictor_config,
        resolve_predictor_config_per_comparison,
    )

    try:
        configs = resolve_predictor_config_per_comparison(project_json)
    except Exception:
        configs = []
    if configs:
        base = configs[0][0]
        return base.model_path, base.model_dir, base

    base = resolve_predictor_config(project_json)
    model_path = base.model_path
    model_dir = base.model_dir
    # Fallback: if model_dir is the detections root, prefer nested comparison dirs
    # that actually contain classifier-*.pkl files.
    if model_path is None and model_dir is not None:
        root = Path(model_dir)
        if root.is_dir() and not any(root.glob("classifier-*.pkl")):
            nested = sorted(
                p.parent
                for p in root.rglob("classifier-*.pkl")
                if p.is_file()
            )
            if nested:
                model_dir = str(nested[0])
    return model_path, model_dir, base


def _score_classic_ecdf_partition(
    *,
    project_json: Path,
    output_dir: Path,
    partition: str,
    control_paths: List[str],
    disease_paths: List[str],
) -> Dict[str, Any]:
    """Score one classic ECDF partition and write canonical train_/test_ artifacts."""
    from methyl_predictor.core.predictor import run_prediction
    from methyl_predictor.models.config import PredictorConfig

    if not control_paths or not disease_paths:
        raise ValueError(f"{partition} partition requires non-empty control and disease paths")

    model_path, model_dir, base_predictor = _resolve_classic_ecdf_model_locations(project_json)
    if model_path is None and model_dir is None:
        raise FileNotFoundError(
            f"Could not resolve classifier model_path/model_dir for {project_json}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(output_dir), prefix=f".{partition}-eval-") as tmp_out:
        tmp_path = Path(tmp_out)
        cfg = PredictorConfig(
            model_path=model_path,
            model_dir=model_dir,
            output_dir=str(tmp_path),
            test_control_paths=list(control_paths),
            test_disease_paths=list(disease_paths),
            test_group_paths=None,
            samples_base_path=base_predictor.samples_base_path,
            path_remap=base_predictor.path_remap,
            debug=bool(base_predictor.debug),
            classifier_step_snapshot=base_predictor.classifier_step_snapshot,
            panel=base_predictor.panel,
            decision_enabled=bool(base_predictor.decision_enabled),
            decision_min_margin=float(base_predictor.decision_min_margin),
            decision_min_confidence=float(base_predictor.decision_min_confidence),
        )
        metrics = run_prediction(cfg)
        pred_src = tmp_path / "predictions.csv"
        metrics_src = tmp_path / "validation_metrics.json"
        if not pred_src.is_file():
            raise FileNotFoundError(f"Predictor did not write predictions.csv for {partition}")
        pred_dst = output_dir / f"{partition}_predictions.csv"
        metrics_dst = output_dir / f"{partition}_metrics.json"
        shutil.copy2(pred_src, pred_dst)
        if metrics_src.is_file():
            payload = json.loads(metrics_src.read_text(encoding="utf-8"))
        else:
            payload = dict(metrics) if isinstance(metrics, dict) else {}
        payload["evaluation_partition"] = partition
        payload["metrics_source"] = f"ecdf_{partition}"
        payload["n_samples"] = int(len(control_paths) + len(disease_paths))
        metrics_dst.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if partition == "test":
            # metrics_dst is already test_metrics.json; only alias the legacy names.
            shutil.copy2(pred_dst, output_dir / "predictions.csv")
            shutil.copy2(metrics_dst, output_dir / "validation_metrics.json")
        return {
            "partition": partition,
            "predictions_csv": str(pred_dst),
            "metrics_json": str(metrics_dst),
            "n_samples": int(len(control_paths) + len(disease_paths)),
        }


def _binary_partition_paths_from_resolver(
    project_json: Path,
    partition: str,
) -> tuple[List[str], List[str]]:
    """
    Resolve control/disease paths for classic ECDF via the shared partition API.

    Uses ``evaluation_partition`` so train comes from project groups and test from
    ``test_groups.json`` (same contract as gene/tabular/generative backends).
    """
    from methyl_utils import load_project

    from .classification_metrics import resolve_class_roles
    from .eval_split_resolver import resolve_eval_paths_and_labels

    project = load_project(project_json)
    class_names = list(resolve_class_roles(project)["class_names"])
    if len(class_names) < 2:
        class_names = ["control", "disease"]
    samples, y_true = resolve_eval_paths_and_labels(
        project_json,
        class_names,
        project_loader=load_project,
        evaluation_partition=partition,
    )
    if y_true is None or not samples:
        raise ValueError(f"No samples resolved for classic ECDF {partition} partition")
    control = [str(s) for s, y in zip(samples, y_true) if int(y) == 0]
    disease = [str(s) for s, y in zip(samples, y_true) if int(y) != 0]
    if not control or not disease:
        raise ValueError(
            f"Classic ECDF {partition} partition requires non-empty control and disease "
            f"paths (control={len(control)}, disease={len(disease)})"
        )
    return control, disease


def _run_classic_ecdf_partitioned_predictor(
    project_json: Path,
    predictor_output_dir: Optional[Path],
) -> tuple[int, str, str]:
    """
    Classic ECDF model-MC scoring: train then test, with canonical artifact names.

    methyl-predictor alone writes undifferentiated predictions.csv; model-MC and the
    covariate second stage require disjoint train_/test_ prediction files.
    """
    try:
        from .eval_split_resolver import assert_model_mc_train_partition

        assert_model_mc_train_partition(project_json)
        output_dir = Path(predictor_output_dir or (Path(project_json).parent / "predictors"))
        train_control, train_disease = _binary_partition_paths_from_resolver(project_json, "train")
        test_control, test_disease = _binary_partition_paths_from_resolver(project_json, "test")
        train_out = _score_classic_ecdf_partition(
            project_json=project_json,
            output_dir=output_dir,
            partition="train",
            control_paths=train_control,
            disease_paths=train_disease,
        )
        test_out = _score_classic_ecdf_partition(
            project_json=project_json,
            output_dir=output_dir,
            partition="test",
            control_paths=test_control,
            disease_paths=test_disease,
        )
        return 0, json.dumps({"train": train_out, "test": test_out}), ""
    except Exception as e:
        return 1, "", str(e)


def build_model_backend_steps(
    *,
    project_json: Path,
    predictor_output_dir: Optional[Path],
    config: Any,
    per_cancer_group: bool,
    run_classifier_fn: Callable[[Path, bool], tuple[int, str, str]],
    run_predictor_fn: Callable[[Path, Optional[Path]], tuple[int, str, str]],
) -> List[TrainerStep]:
    backend = (config.model_backend if config is not None else "ecdf").strip().lower()
    if backend == "survival":
        backend = "cox"
    feature_mode = (config.feature_mode if config is not None else "raw_dmp").strip().lower()
    feature_family_set = _normalized_feature_family_set(config)
    if backend == "cox":
        model_dir = _classifier_output_dir(project_json, predictor_output_dir)

        def _run_cox() -> tuple[int, str, str]:
            try:
                from .survival_backend import run_survival_model

                metrics = run_survival_model(
                    project_json=project_json,
                    output_dir=model_dir,
                    config=config,
                )
                return 0, json.dumps(metrics), ""
            except Exception as e:
                return 1, "", str(e)

        return [("cox-survival", _run_cox)]
    if backend == "tabular_sklearn":
        model_dir = _classifier_output_dir(project_json, predictor_output_dir)

        def _run_tabular_bundle() -> tuple[int, str, str]:
            try:
                from .model_bundle import build_model_feature_bundle

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                build_model_feature_bundle(
                    project_json=project_json,
                    output_dir=bundle_dir,
                    weight_column=(config.model_weight_column if config is not None else "effect_size"),
                    feature_family_set=feature_family_set,
                    require_mapper_annotations=_requires_mapper_annotations(feature_family_set),
                    extra_metadata={
                        "model_backend": "tabular_sklearn",
                        "feature_mode": (config.feature_mode if config is not None else "raw_dmp"),
                        "feature_family_set": feature_family_set,
                    },
                )
                return 0, f"Bundle written to {bundle_dir}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_tabular_train() -> tuple[int, str, str]:
            try:
                from .tabular_backend import train_tabular_model

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                model_path = train_tabular_model(
                    project_json=project_json,
                    bundle_h5=bundle_dir / "model_feature_bundle.h5",
                    bundle_dir=bundle_dir,
                    output_dir=model_dir,
                    model_type=(config.tabular_model_type if config is not None else "random_forest"),
                    tabular_methods=(config.tabular_methods if config is not None else None),
                    tabular_method_selection_metric=(
                        config.tabular_method_selection_metric if config is not None else "balanced_accuracy"
                    ),
                    tabular_method_selection_stat=(
                        config.tabular_method_selection_stat if config is not None else "mean"
                    ),
                    max_dmps=(config.tabular_max_dmps if config is not None else 0),
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.covariates_strict_join if config is not None else False),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    covariate_numeric_columns=(config.covariate_numeric_columns if config is not None else None),
                    covariate_ordinal_columns=(config.covariate_ordinal_columns if config is not None else None),
                    covariate_ordinal_maps=(config.covariate_ordinal_maps if config is not None else None),
                    covariate_ordinal_unknown_value=(config.covariate_ordinal_unknown_value if config is not None else 0.0),
                    covariate_categorical_columns=(config.covariate_categorical_columns if config is not None else None),
                    covariate_missing_numeric_strategy=(config.covariate_missing_numeric_strategy if config is not None else "mean"),
                    covariate_standardize_numeric=(config.covariate_standardize_numeric if config is not None else True),
                    covariate_composition_groups=(
                        config.covariate_composition_groups if config is not None else None
                    ),
                    covariate_composition_transform=(
                        config.covariate_composition_transform if config is not None else None
                    ),
                    covariate_composition_columns=(
                        config.covariate_composition_columns if config is not None else None
                    ),
                    covariate_composition_reference=(
                        config.covariate_composition_reference if config is not None else None
                    ),
                    covariate_composition_pseudocount=(
                        config.covariate_composition_pseudocount if config is not None else None
                    ),
                    feature_mode=(config.feature_mode if config is not None else "raw_dmp"),
                    observed_feature_quantiles=(config.observed_feature_quantiles if config is not None else None),
                    observed_feature_min_coverage=(config.observed_feature_min_coverage if config is not None else 1),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                    observed_feature_include_dmp=(
                        config.observed_feature_include_dmp if config is not None else True
                    ),
                    observed_feature_include_chromosome=(
                        config.observed_feature_include_chromosome if config is not None else True
                    ),
                    observed_feature_include_dmr=(
                        config.observed_feature_include_dmr if config is not None else True
                    ),
                    observed_feature_include_gene=(
                        config.observed_feature_include_gene if config is not None else True
                    ),
                    observed_feature_dmr_window_bp=(
                        config.observed_feature_dmr_window_bp if config is not None else 100000
                    ),
                    observed_feature_max_dmrs=(
                        config.observed_feature_max_dmrs if config is not None else 32
                    ),
                    observed_feature_max_genes=(
                        config.observed_feature_max_genes if config is not None else 32
                    ),
                    feature_family_set=feature_family_set,
                    gene_feature_loading=(config.gene_feature_loading if config is not None else "frozen"),
                    observed_hist_eps=(config.observed_hist_eps if config is not None else 1e-6),
                    observed_hist_alpha=(config.observed_hist_alpha if config is not None else 0.5),
                    observed_hist_evidence_clip_cap=(
                        config.observed_hist_evidence_clip_cap if config is not None else 5.0
                    ),
                    observed_hist_tail_agreement_threshold=(
                        config.observed_hist_tail_agreement_threshold if config is not None else 0.10
                    ),
                    gene_scored_min_support_n=(
                        config.gene_scored_min_support_n if config is not None else 2
                    ),
                    gene_scored_use_region_weight=(
                        config.gene_scored_use_region_weight if config is not None else True
                    ),
                    gene_scored_gene_weight=(
                        config.gene_scored_gene_weight if config is not None else "importance_x_sqrt_support"
                    ),
                    gene_scored_ordered_comparison_labels=(
                        config.gene_scored_ordered_comparison_labels if config is not None else None
                    ),
                    gene_scored_contrast_pairs=(
                        config.gene_scored_contrast_pairs if config is not None else None
                    ),
                    structural_scored_min_support_n=(
                        config.structural_scored_min_support_n if config is not None else 2
                    ),
                    structural_scored_use_region_weight=(
                        config.structural_scored_use_region_weight if config is not None else True
                    ),
                    structural_scored_weight=(
                        config.structural_scored_weight if config is not None else "compound_x_sqrt_support"
                    ),
                    structural_scored_ordered_comparison_labels=(
                        config.structural_scored_ordered_comparison_labels if config is not None else None
                    ),
                    structural_scored_contrast_pairs=(
                        config.structural_scored_contrast_pairs if config is not None else None
                    ),
                    region_directional_region_types=(
                        config.region_directional_region_types if config is not None else None
                    ),
                    region_directional_min_loci=(
                        config.region_directional_min_loci if config is not None else 1
                    ),
                    observed_feature_quality_columns=(
                        config.observed_feature_quality_columns if config is not None else None
                    ),
                    chromosome_hypo_beta_threshold=(
                        config.chromosome_hypo_beta_threshold if config is not None else None
                    ),
                    chromosome_intermediate_beta_lo=(
                        config.chromosome_intermediate_beta_lo if config is not None else None
                    ),
                    chromosome_intermediate_beta_hi=(
                        config.chromosome_intermediate_beta_hi if config is not None else None
                    ),
                    chromosome_distance_metrics=(
                        config.chromosome_distance_metrics if config is not None else None
                    ),
                    chromosome_list=(config.chromosome_list if config is not None else None),
                    save_train_dataset=(
                        bool(config.tabular_save_train_dataset)
                        if config is not None
                        else False
                    ),
                    reuse_train_dataset=(
                        bool(config.tabular_reuse_train_dataset)
                        if config is not None
                        else True
                    ),
                    train_dataset_path=(
                        config.tabular_train_dataset_path
                        if config is not None
                        else None
                    ),
                    save_test_dataset=(
                        bool(config.tabular_save_test_dataset)
                        if config is not None
                        else True
                    ),
                    test_dataset_path=(
                        config.tabular_test_dataset_path
                        if config is not None
                        else None
                    ),
                )
                return 0, f"Tabular model trained: {model_path}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_tabular_predict() -> tuple[int, str, str]:
            try:
                from .tabular_backend import predict_tabular_model_from_project

                output_dir = predictor_output_dir or (project_json.parent / "predictors")
                common = dict(
                    project_json=project_json,
                    model_dir=model_dir,
                    output_dir=output_dir,
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.covariates_strict_join if config is not None else False),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                )
                train_metrics = predict_tabular_model_from_project(
                    **common,
                    evaluation_partition="train",
                )
                test_metrics = predict_tabular_model_from_project(
                    **common,
                    evaluation_partition="test",
                )
                return 0, json.dumps({"train": train_metrics, "test": test_metrics}), ""
            except Exception as e:
                return 1, "", str(e)

        return [
            ("model-bundle", _run_tabular_bundle),
            ("tabular-train", _run_tabular_train),
            ("tabular-predictor", _run_tabular_predict),
        ]

    if backend == "generative_hybrid":
        model_dir = _classifier_output_dir(project_json, predictor_output_dir)

        def _run_generative_bundle() -> tuple[int, str, str]:
            try:
                from .model_bundle import build_model_feature_bundle

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                build_model_feature_bundle(
                    project_json=project_json,
                    output_dir=bundle_dir,
                    weight_column=(config.model_weight_column if config is not None else "effect_size"),
                    feature_family_set=feature_family_set,
                    require_mapper_annotations=_requires_mapper_annotations(feature_family_set),
                    extra_metadata={
                        "model_backend": "generative_hybrid",
                        "feature_mode": (config.feature_mode if config is not None else "raw_dmp"),
                        "feature_family_set": feature_family_set,
                    },
                )
                return 0, f"Bundle written to {bundle_dir}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_generative_train() -> tuple[int, str, str]:
            try:
                from .generative_backend import train_generative_model

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                model_path = train_generative_model(
                    project_json=project_json,
                    bundle_h5=bundle_dir / "model_feature_bundle.h5",
                    output_dir=model_dir,
                    max_dmps=(config.tabular_max_dmps if config is not None else 0),
                    latent_dim=(config.generative_latent_dim if config is not None else 16),
                    kl_weight=(config.generative_kl_weight if config is not None else 0.1),
                    density_type=(config.generative_density_type if config is not None else "diag_gaussian"),
                    epochs=(config.generative_epochs if config is not None else 50),
                    batch_size=(config.generative_batch_size if config is not None else 64),
                    random_seed=(config.generative_seed if config is not None else 13),
                    calibrate=(config.generative_calibrate if config is not None else False),
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.generative_covariates_strict if config is not None else True),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    covariate_numeric_columns=(config.covariate_numeric_columns if config is not None else None),
                    covariate_ordinal_columns=(config.covariate_ordinal_columns if config is not None else None),
                    covariate_ordinal_maps=(config.covariate_ordinal_maps if config is not None else None),
                    covariate_ordinal_unknown_value=(config.covariate_ordinal_unknown_value if config is not None else 0.0),
                    covariate_categorical_columns=(config.covariate_categorical_columns if config is not None else None),
                    covariate_missing_numeric_strategy=(config.covariate_missing_numeric_strategy if config is not None else "mean"),
                    covariate_standardize_numeric=(config.covariate_standardize_numeric if config is not None else True),
                    covariate_composition_groups=(
                        config.covariate_composition_groups if config is not None else None
                    ),
                    covariate_composition_transform=(
                        config.covariate_composition_transform if config is not None else None
                    ),
                    covariate_composition_columns=(
                        config.covariate_composition_columns if config is not None else None
                    ),
                    covariate_composition_reference=(
                        config.covariate_composition_reference if config is not None else None
                    ),
                    covariate_composition_pseudocount=(
                        config.covariate_composition_pseudocount if config is not None else None
                    ),
                    feature_mode=(config.feature_mode if config is not None else "raw_dmp"),
                    observed_feature_quantiles=(config.observed_feature_quantiles if config is not None else None),
                    observed_feature_min_coverage=(config.observed_feature_min_coverage if config is not None else 1),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                    observed_feature_include_dmp=(
                        config.observed_feature_include_dmp if config is not None else True
                    ),
                    observed_feature_include_chromosome=(
                        config.observed_feature_include_chromosome if config is not None else True
                    ),
                    observed_feature_include_dmr=(
                        config.observed_feature_include_dmr if config is not None else True
                    ),
                    observed_feature_include_gene=(
                        config.observed_feature_include_gene if config is not None else True
                    ),
                    observed_feature_dmr_window_bp=(
                        config.observed_feature_dmr_window_bp if config is not None else 100000
                    ),
                    observed_feature_max_dmrs=(
                        config.observed_feature_max_dmrs if config is not None else 32
                    ),
                    observed_feature_max_genes=(
                        config.observed_feature_max_genes if config is not None else 32
                    ),
                    feature_family_set=feature_family_set,
                    gene_feature_loading=(config.gene_feature_loading if config is not None else "frozen"),
                    observed_hist_eps=(config.observed_hist_eps if config is not None else 1e-6),
                    observed_hist_alpha=(config.observed_hist_alpha if config is not None else 0.5),
                    observed_hist_evidence_clip_cap=(
                        config.observed_hist_evidence_clip_cap if config is not None else 5.0
                    ),
                    observed_hist_tail_agreement_threshold=(
                        config.observed_hist_tail_agreement_threshold if config is not None else 0.10
                    ),
                    gene_scored_min_support_n=(
                        config.gene_scored_min_support_n if config is not None else 2
                    ),
                    gene_scored_use_region_weight=(
                        config.gene_scored_use_region_weight if config is not None else True
                    ),
                    gene_scored_gene_weight=(
                        config.gene_scored_gene_weight if config is not None else "importance_x_sqrt_support"
                    ),
                    gene_scored_ordered_comparison_labels=(
                        config.gene_scored_ordered_comparison_labels if config is not None else None
                    ),
                    gene_scored_contrast_pairs=(
                        config.gene_scored_contrast_pairs if config is not None else None
                    ),
                    structural_scored_min_support_n=(
                        config.structural_scored_min_support_n if config is not None else 2
                    ),
                    structural_scored_use_region_weight=(
                        config.structural_scored_use_region_weight if config is not None else True
                    ),
                    structural_scored_weight=(
                        config.structural_scored_weight if config is not None else "compound_x_sqrt_support"
                    ),
                    structural_scored_ordered_comparison_labels=(
                        config.structural_scored_ordered_comparison_labels if config is not None else None
                    ),
                    structural_scored_contrast_pairs=(
                        config.structural_scored_contrast_pairs if config is not None else None
                    ),
                    region_directional_region_types=(
                        config.region_directional_region_types if config is not None else None
                    ),
                    region_directional_min_loci=(
                        config.region_directional_min_loci if config is not None else 1
                    ),
                    observed_feature_quality_columns=(
                        config.observed_feature_quality_columns if config is not None else None
                    ),
                    chromosome_hypo_beta_threshold=(
                        config.chromosome_hypo_beta_threshold if config is not None else None
                    ),
                    chromosome_intermediate_beta_lo=(
                        config.chromosome_intermediate_beta_lo if config is not None else None
                    ),
                    chromosome_intermediate_beta_hi=(
                        config.chromosome_intermediate_beta_hi if config is not None else None
                    ),
                    chromosome_distance_metrics=(
                        config.chromosome_distance_metrics if config is not None else None
                    ),
                    chromosome_list=(config.chromosome_list if config is not None else None),
                )
                return 0, f"Generative model trained: {model_path}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_generative_predict() -> tuple[int, str, str]:
            try:
                from .generative_backend import predict_generative_model_from_project

                output_dir = predictor_output_dir or (project_json.parent / "predictors")
                common = dict(
                    project_json=project_json,
                    model_dir=model_dir,
                    output_dir=output_dir,
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.generative_covariates_strict if config is not None else True),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                )
                train_metrics = predict_generative_model_from_project(
                    **common,
                    evaluation_partition="train",
                )
                test_metrics = predict_generative_model_from_project(
                    **common,
                    evaluation_partition="test",
                )
                return 0, json.dumps({"train": train_metrics, "test": test_metrics}), ""
            except Exception as e:
                return 1, "", str(e)

        return [
            ("model-bundle", _run_generative_bundle),
            ("generative-train", _run_generative_train),
            ("generative-predictor", _run_generative_predict),
        ]

    ecdf_aggregated_enabled = (
        bool(config.ecdf_aggregated_enabled)
        if config is not None and config.ecdf_aggregated_enabled is not None
        else False
    )

    def _run_ecdf_second_stage() -> tuple[int, str, str]:
        from .ecdf_second_stage import (
            EcdfSecondStageParams,
            ecdf_second_stage_should_run,
            train_and_apply_ecdf_second_stage,
        )

        classifier_output_dir = _classifier_output_dir(project_json, predictor_output_dir)
        # Classic raw_dmp path still records training metrics even when second-stage is off.
        write_training_metrics = backend == "ecdf" and feature_mode != "raw_gene" and not ecdf_aggregated_enabled
        tm_ok, tm_msg = (False, "")
        if write_training_metrics:
            tm_ok, tm_msg = _write_ecdf_training_metrics(project_json, classifier_output_dir)
        try:
            if not ecdf_second_stage_should_run(config):
                if write_training_metrics:
                    if tm_ok:
                        return 0, f"ECDF second-stage scorer disabled. Training metrics saved: {tm_msg}", ""
                    return 0, f"ECDF second-stage scorer disabled. {tm_msg}", ""
                return (
                    0,
                    "ECDF second-stage scorer skipped (set ecdf_second_stage_enabled=true; "
                    "covariates_path alone is coerced to enable when present on resolved config).",
                    "",
                )
            params = EcdfSecondStageParams.from_monte_carlo_config(
                config,
                feature_family_set=feature_family_set,
            )
            out = train_and_apply_ecdf_second_stage(
                project_json=project_json,
                predictor_output_dir=(predictor_output_dir or (project_json.parent / "predictors")),
                classifier_output_dir=classifier_output_dir,
                params=params,
            )
            if isinstance(out, dict):
                out["training_metrics_saved"] = bool(tm_ok)
                out["training_metrics_path"] = tm_msg if tm_ok else None
                out["training_metrics_note"] = None if tm_ok else (tm_msg or None)
            return 0, json.dumps(out), ""
        except Exception as e:
            if write_training_metrics and not tm_ok:
                return 1, "", f"ECDF second-stage failed: {e}. {tm_msg}"
            if write_training_metrics and tm_ok:
                return 1, "", f"ECDF second-stage failed: {e}. Training metrics saved: {tm_msg}"
            return 1, "", f"ECDF second-stage failed: {e}"

    if backend == "ecdf" and feature_mode == "raw_gene":
        model_dir = (
            predictor_output_dir.parent / "classifiers"
            if predictor_output_dir is not None
            else project_json.parent / "classifiers"
        )

        def _run_ecdf_gene_bundle() -> tuple[int, str, str]:
            try:
                from .model_bundle import build_model_feature_bundle

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                build_model_feature_bundle(
                    project_json=project_json,
                    output_dir=bundle_dir,
                    weight_column=(config.model_weight_column if config is not None else "effect_size"),
                    feature_family_set="gene_scored",
                    require_mapper_annotations=True,
                    extra_metadata={
                        "model_backend": "ecdf",
                        "classifier_type": "ecdf_gene_one_vs_rest",
                        "feature_mode": "raw_gene",
                        "feature_family_set": "gene_scored",
                    },
                )
                return 0, f"Bundle written to {bundle_dir}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_ecdf_gene_train() -> tuple[int, str, str]:
            try:
                from .ecdf_gene_backend import train_ecdf_gene_ovr_model

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                model_path = train_ecdf_gene_ovr_model(
                    project_json=project_json,
                    bundle_h5=bundle_dir / "model_feature_bundle.h5",
                    output_dir=model_dir,
                    min_coverage=(
                        config.observed_feature_min_coverage if config is not None else 1
                    ),
                    use_region_weight=True,
                    gene_weight_column="gene_importance",
                    n_bins=(config.ecdf_aggregated_n_bins if config is not None else 100),
                    temperature=1.0,
                )
                return 0, f"Gene ECDF model trained: {model_path}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_ecdf_gene_predict() -> tuple[int, str, str]:
            try:
                from .ecdf_gene_backend import predict_ecdf_gene_ovr_from_project

                model_path = model_dir / "ecdf_gene_ovr.pkl"
                output_dir = predictor_output_dir or (project_json.parent / "predictors")
                train_metrics = predict_ecdf_gene_ovr_from_project(
                    project_json=project_json,
                    model_path=model_path,
                    output_dir=output_dir,
                    evaluation_partition="train",
                )
                test_metrics = predict_ecdf_gene_ovr_from_project(
                    project_json=project_json,
                    model_path=model_path,
                    output_dir=output_dir,
                    evaluation_partition="test",
                )
                return 0, json.dumps({"train": train_metrics, "test": test_metrics}), ""
            except Exception as e:
                return 1, "", str(e)

        return [
            ("model-bundle", _run_ecdf_gene_bundle),
            ("ecdf-gene-train", _run_ecdf_gene_train),
            ("ecdf-gene-predictor", _run_ecdf_gene_predict),
            ("ecdf-second-stage", _run_ecdf_second_stage),
        ]

    if backend == "ecdf" and ecdf_aggregated_enabled:
        model_dir = (
            predictor_output_dir.parent / "classifiers"
            if predictor_output_dir is not None
            else project_json.parent / "classifiers"
        )

        def _run_ecdf_aggregated_bundle() -> tuple[int, str, str]:
            try:
                from .model_bundle import build_model_feature_bundle

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                build_model_feature_bundle(
                    project_json=project_json,
                    output_dir=bundle_dir,
                    weight_column=(config.model_weight_column if config is not None else "effect_size"),
                    feature_family_set=feature_family_set,
                    require_mapper_annotations=_requires_mapper_annotations(feature_family_set),
                    extra_metadata={
                        "model_backend": "ecdf",
                        "classifier_type": "ecdf_aggregated_one_vs_rest",
                        "feature_mode": feature_mode,
                        "feature_family_set": feature_family_set,
                    },
                )
                return 0, f"Bundle written to {bundle_dir}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_ecdf_aggregated_train() -> tuple[int, str, str]:
            try:
                from .ecdf_aggregated_backend import train_ecdf_aggregated_ovr_model

                bundle_dir = (
                    Path(config.model_bundle_dir)
                    if config is not None and config.model_bundle_dir
                    else (project_json.parent / "model_bundle")
                )
                model_path = train_ecdf_aggregated_ovr_model(
                    project_json=project_json,
                    bundle_h5=bundle_dir / "model_feature_bundle.h5",
                    output_dir=model_dir,
                    feature_family_set=feature_family_set,
                    observed_feature_min_coverage=(
                        config.observed_feature_min_coverage if config is not None else 1
                    ),
                    observed_feature_quality_columns=(
                        config.observed_feature_quality_columns if config is not None else None
                    ),
                    observed_hist_eps=(config.observed_hist_eps if config is not None else 1e-6),
                    observed_hist_alpha=(config.observed_hist_alpha if config is not None else 0.5),
                    observed_hist_evidence_clip_cap=(
                        config.observed_hist_evidence_clip_cap if config is not None else 5.0
                    ),
                    observed_hist_tail_agreement_threshold=(
                        config.observed_hist_tail_agreement_threshold if config is not None else 0.10
                    ),
                    n_bins=(config.ecdf_aggregated_n_bins if config is not None else 100),
                    temperature=1.0,
                )
                return 0, f"Aggregated ECDF model trained: {model_path}", ""
            except Exception as e:
                return 1, "", str(e)

        def _run_ecdf_aggregated_predict() -> tuple[int, str, str]:
            try:
                from .ecdf_aggregated_backend import predict_ecdf_aggregated_ovr_from_project

                model_path = model_dir / "ecdf_aggregated_ovr.pkl"
                output_dir = predictor_output_dir or (project_json.parent / "predictors")
                train_metrics = predict_ecdf_aggregated_ovr_from_project(
                    project_json=project_json,
                    model_path=model_path,
                    output_dir=output_dir,
                    evaluation_partition="train",
                )
                test_metrics = predict_ecdf_aggregated_ovr_from_project(
                    project_json=project_json,
                    model_path=model_path,
                    output_dir=output_dir,
                    evaluation_partition="test",
                )
                return 0, json.dumps({"train": train_metrics, "test": test_metrics}), ""
            except Exception as e:
                return 1, "", str(e)

        return [
            ("model-bundle", _run_ecdf_aggregated_bundle),
            ("ecdf-aggregated-train", _run_ecdf_aggregated_train),
            ("ecdf-aggregated-predictor", _run_ecdf_aggregated_predict),
            ("ecdf-second-stage", _run_ecdf_second_stage),
        ]

    def _run_classic_predictor() -> tuple[int, str, str]:
        # Prefer partitioned train/test scoring when MC sidecars are present.
        run_dir = Path(project_json).resolve().parent
        if (run_dir / "train_control.csv").is_file() and (
            (run_dir / "test_control.csv").is_file() or (run_dir / "val_control.csv").is_file()
        ):
            return _run_classic_ecdf_partitioned_predictor(project_json, predictor_output_dir)
        return run_predictor_fn(project_json, predictor_output_dir)

    return [
        ("methyl-classifier", lambda: run_classifier_fn(project_json, per_cancer_group)),
        ("methyl-predictor", _run_classic_predictor),
        ("ecdf-second-stage", _run_ecdf_second_stage),
    ]
