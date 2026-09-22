"""
Build ValidationPipeline context_json (iterations[]) from Monte Carlo stratified splits.

Used by:
- workers/methyl_worker ``validation.plan-iterations`` capability
- middle-tier REST ``POST /v1/validation/plan-iterations``
- ``methyl-validation plan-workflow-context`` CLI
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from methyl_domain.types import McIterationTaskConfig

from .cohort_inference import infer_monte_carlo_cohorts_from_project
from .config import MonteCarloConfig, ValidationStepConfig, parse_validation_profile
from .mc_config_load import apply_project_regulatory_to_mc_dict, write_mc_config_snapshot
from .utils.migrate_backend_config import (
    LEGACY_BACKEND_KEYS,
    merge_legacy_validation_keys_into_backend_profiles,
)
from .mc_manifest import write_detector_featurecuts_override, write_mapper_classifier_override, resolve_detector_step_override_path
from .planner_models import (
    ValidationPlanContext,
    ValidationPlannedIteration,
    ValidationPlanRequest,
    ValidationPlanSummary,
)
from .project_gen import (
    _run_scoped_artifact_dir,
    _side_by_label,
    build_centroid_seed_groups,
    build_cohort_relative_centroid_scope,
    build_group_centroid_scope,
    carry_forward_centroids_from_previous_run,
    centroid_override_has_remove_samples,
    generate_run_project,
    generate_run_project_hierarchical_multiclass,
    generate_run_project_multiclass,
    infer_monte_carlo_layout,
    run_project_needs_regeneration,
    prepare_incremental_centroid_baseline,
)
from .reuse_splits import try_load_binary_split_from_run_dir, try_load_multiclass_split_from_run_dir
from .split import load_and_resolve_sample_paths, stratified_split, stratified_split_multiclass
from .storage_layout import mc_config_snapshot_path

__all__ = [
    "ValidationPlanRequest",
    "ValidationPlanContext",
    "ValidationPlannedIteration",
    "ValidationPlanSummary",
    "plan_validation_context",
    "resolve_base_project_json",
]


def resolve_base_project_json(project_path: str | Path) -> Path:
    """Resolve projectPath to the base project.json file."""
    path = Path(project_path).expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".json":
        return path
    if path.is_dir():
        candidate = path / "project.json"
        if candidate.is_file():
            return candidate
        configs = sorted(path.glob("project*.json"))
        if len(configs) == 1:
            return configs[0]
        if configs:
            return configs[0]
    raise FileNotFoundError(f"Could not resolve base project.json from projectPath={project_path!r}")


def _product_path_str(path: Path | str) -> str:
    """Absolute product path without following a leaf CAAS symlink.

    ``Path.resolve()`` on ``monte_carlo_runs/run_XXXX/project.json`` follows the
    content-store symlink into ``.caas/validation_plan_iterations/<key>/...``. Downstream
    centroid/detector tasks then depend on CAAS blob paths that a later content-key
    commit can steal or delete.
    """
    path = Path(path)
    return str(path.parent.resolve() / path.name)


def _parallel_mc_centroid_seed_enabled(config: MonteCarloConfig) -> bool:
    if config.parallel_mc_centroid_seed is None:
        return True
    return bool(config.parallel_mc_centroid_seed)


def _load_config_from_project(
    base_project: Path,
    request: ValidationPlanRequest,
    *,
    profile_overrides: Optional[ValidationStepConfig] = None,
) -> MonteCarloConfig:
    from methyl_utils import load_project
    from methyl_utils.action_config_resolver import resolve_for_project

    project = load_project(str(base_project))
    if profile_overrides is not None:
        validation = profile_overrides.model_dump(exclude_none=True, exclude_unset=True)
    else:
        validation = resolve_for_project("validation", project)
    if not validation:
        raise ValueError(f"Project {base_project} missing resolved validation action config")

    with open(base_project, encoding="utf-8") as f:
        project_data = json.load(f)

    cohorts = infer_monte_carlo_cohorts_from_project(project_data, str(base_project))
    if len(cohorts) < 2:
        raise ValueError(
            "Could not infer >=2 Monte Carlo cohorts from project controls/diseases sample_paths."
        )

    if any(k in validation for k in LEGACY_BACKEND_KEYS):
        validation, _ = merge_legacy_validation_keys_into_backend_profiles(validation)

    mc_dict = apply_project_regulatory_to_mc_dict(
        {
            "samples_base_path": project_data.get("samples_base_path", "/work/samples"),
            "base_project": str(base_project),
            "output_base": project_data.get("output_base", "/work/projects/prostate-cancer"),
            "path_remap": project_data.get("path_remap"),
            "cohorts": cohorts,
            **dict(validation),
        },
        project,
    )
    # Planner-level fields (train_fraction, n_iterations, seed) may live only in the
    # instance context at plan time, not in the profile's validation actionConfig.
    # Aggregation steps re-resolve from the profile and pass these via the request
    # (backfilled from the MC config snapshot); seed them so model validation succeeds.
    if mc_dict.get("n_iterations") is None and request.featureIterations is not None:
        mc_dict["n_iterations"] = int(request.featureIterations)
    if mc_dict.get("train_fraction") is None and request.trainFraction is not None:
        mc_dict["train_fraction"] = float(request.trainFraction)
    if mc_dict.get("seed") is None and request.seed is not None:
        mc_dict["seed"] = int(request.seed)
    config = MonteCarloConfig.model_validate(mc_dict)

    feature_n = request.featureIterations if request.featureIterations is not None else config.n_iterations
    updates: Dict[str, Any] = {"n_iterations": int(feature_n)}
    if request.seed is not None:
        updates["seed"] = int(request.seed)
    if request.trainFraction is not None:
        updates["train_fraction"] = float(request.trainFraction)
    return config.model_copy(update=updates)


def _monte_carlo_runs_root(config: MonteCarloConfig, base_project: Path) -> Tuple[Path, str]:
    from methyl_utils import load_project

    project_cfg = load_project(str(base_project))
    output_base = Path(config.output_base)
    output_base.mkdir(parents=True, exist_ok=True)
    runs_root = output_base / project_cfg.project_name / "monte_carlo_runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    project_root = str(output_base / project_cfg.project_name)
    return runs_root, project_root


def _build_task_config(
    *,
    display_run_id: str,
    phase: str,
    phase_index: int,
    layout: str,
    config: MonteCarloConfig,
    seed_i: Optional[int],
    project_path: Path,
    run_dir: Path,
    monte_carlo_runs_root: Path,
    detector_step_override_path: Optional[str] = None,
    val_control_csv: Optional[Path] = None,
    val_disease_csv: Optional[Path] = None,
    val_groups_json: Optional[Path] = None,
    c1: Optional[Path] = None,
    c2: Optional[Path] = None,
    centroid_overrides_by_label: Optional[Dict[str, Path]] = None,
) -> McIterationTaskConfig:
    payload: Dict[str, Any] = {
        "runId": display_run_id,
        "phase": phase,
        "iteration": phase_index,
        "layout": layout,
        "trainFraction": config.train_fraction,
        "seed": seed_i,
        "projectJson": _product_path_str(project_path),
        "runDir": _product_path_str(run_dir),
        "monteCarloRunsRoot": _product_path_str(monte_carlo_runs_root),
    }
    if val_control_csv is not None:
        payload["testControlCsv"] = str(val_control_csv)
    if val_disease_csv is not None:
        payload["testDiseaseCsv"] = str(val_disease_csv)
    if val_groups_json is not None:
        payload["testGroupsJson"] = str(val_groups_json)
    if c1 is not None:
        payload["centroidGroup1Override"] = str(c1)
    if c2 is not None:
        payload["centroidGroup2Override"] = str(c2)
    if centroid_overrides_by_label:
        payload["centroidOverridesByLabel"] = {
            lbl: str(path) for lbl, path in centroid_overrides_by_label.items()
        }
    if detector_step_override_path is not None:
        payload["detectorStepOverride"] = detector_step_override_path
    return McIterationTaskConfig.model_validate(payload)


def _attach_iteration_centroid_scope(
    iteration: ValidationPlannedIteration,
    *,
    layout: str,
    base_project_path: Path,
    run_dir: Path,
    cohort_labels: List[str],
    train_by_label: Dict[str, List[str]],
    parallel_seed: bool,
    full_cohort_by_label: Optional[Dict[str, List[str]]] = None,
    seed_dir_by_label: Optional[Dict[str, str]] = None,
    previous_train_by_label: Optional[Dict[str, List[str]]] = None,
) -> None:
    """Attach per-group centroid scope and run-scoped detect dirs for workflow iterations."""
    from methyl_utils import load_project

    project = load_project(str(base_project_path))
    if parallel_seed:
        if full_cohort_by_label is None or seed_dir_by_label is None:
            raise ValueError("parallel MC centroid seed requires full_cohort_by_label and seed_dir_by_label")
        iteration.centroidGroups = build_cohort_relative_centroid_scope(
            base_project_path=base_project_path,
            run_dir=run_dir,
            cohort_labels=cohort_labels,
            full_cohort_by_label=full_cohort_by_label,
            train_by_label=train_by_label,
            seed_dir_by_label=seed_dir_by_label,
            project=project,
        )
    else:
        iteration.centroidGroups = build_group_centroid_scope(
            base_project_path=base_project_path,
            run_dir=run_dir,
            cohort_labels=cohort_labels,
            train_by_label=train_by_label,
            previous_train_by_label=previous_train_by_label,
            project=project,
        )
    if layout == "binary" and len(cohort_labels) >= 2:
        control_label, disease_label = cohort_labels[0], cohort_labels[1]
        side_by_label = _side_by_label(project)
        iteration.centroid1Dir = _run_scoped_artifact_dir(
            run_dir,
            project.get_centroid_dir(side_by_label.get(control_label, "control"), control_label),
            anchor="centroids",
            fallback_name=control_label,
        )
        iteration.centroid2Dir = _run_scoped_artifact_dir(
            run_dir,
            project.get_centroid_dir(side_by_label.get(disease_label, "disease"), disease_label),
            anchor="centroids",
            fallback_name=disease_label,
        )
        iteration.detectOutDir = _run_scoped_artifact_dir(
            run_dir,
            project.get_detection_output_dir(control_label, disease_label),
            anchor="detections",
            fallback_name=f"{control_label}_{disease_label}",
        )


def _tag_iteration_as_stratified_draw(
    iteration: ValidationPlannedIteration,
    *,
    run_dir: Path,
    layout: str,
    cohort_labels: List[str],
    config: MonteCarloConfig,
    seed: Optional[int],
) -> ValidationPlannedIteration:
    """Attach tagged ``StratifiedCohortDraw`` groups and comparisons."""
    from methyl_domain.helpers import (
        build_stratified_cohort_draw,
        comparisons_from_project_json,
        groups_from_mc_run_dir,
    )

    project_path = Path(iteration.projectPath)
    groups = groups_from_mc_run_dir(
        run_dir, project_path, layout=layout, cohort_labels=cohort_labels
    )
    comparisons = comparisons_from_project_json(project_path)
    tagged = build_stratified_cohort_draw(
        run_id=iteration.runId,
        phase=iteration.phase,
        project_path=_product_path_str(project_path),
        groups=groups,
        comparisons=comparisons,
        seed=seed,
        train_fraction=config.train_fraction,
        task_config=iteration.taskConfig,
    )
    payload = {**iteration.model_dump(mode="json", by_alias=True), **tagged}
    return ValidationPlannedIteration.model_validate(payload)


def _rehydrate_iteration_centroid_scope(
    iteration: ValidationPlannedIteration,
    *,
    layout: str,
    run_dir: Path,
    base_project_path: Path,
    cohort_paths_list: List[Tuple[str, List[str]]],
    cohort_labels: List[str],
    samples_base_path: str,
    parallel_seed: bool,
    full_cohort_by_label: Dict[str, List[str]],
    seed_dir_by_label: Dict[str, str],
    previous_run_dir: Optional[Path],
) -> None:
    """Rebuild centroidGroups when reusing an existing run directory."""
    train_by_label: Dict[str, List[str]]
    previous_train_by_label: Optional[Dict[str, List[str]]] = None

    if layout == "binary":
        train_by_label = {cohort_labels[0]: [], cohort_labels[1]: []}
        split = try_load_binary_split_from_run_dir(
            run_dir,
            cohort_paths_list[0][1],
            cohort_paths_list[1][1],
            samples_base_path,
        )
        if split is not None:
            train_control, train_disease, _, _ = split
            train_by_label = {
                cohort_labels[0]: list(train_control),
                cohort_labels[1]: list(train_disease),
            }
            if not parallel_seed and previous_run_dir is not None:
                prev_split = try_load_binary_split_from_run_dir(
                    previous_run_dir,
                    cohort_paths_list[0][1],
                    cohort_paths_list[1][1],
                    samples_base_path,
                )
                if prev_split is not None:
                    previous_train_by_label = {
                        cohort_labels[0]: list(prev_split[0]),
                        cohort_labels[1]: list(prev_split[1]),
                    }
    elif layout in {"multiclass", "hierarchical_multiclass"}:
        train_by_label = {lbl: [] for lbl in cohort_labels}
        loaded = try_load_multiclass_split_from_run_dir(
            run_dir, cohort_paths_list, cohort_labels, samples_base_path
        )
        if loaded is not None:
            train_m, _ = loaded
            train_by_label = {lbl: list(train_m[lbl]) for lbl in cohort_labels}
            if not parallel_seed and previous_run_dir is not None:
                prev_loaded = try_load_multiclass_split_from_run_dir(
                    previous_run_dir, cohort_paths_list, cohort_labels, samples_base_path
                )
                if prev_loaded is not None:
                    previous_train_by_label = {
                        lbl: list(prev_loaded[0][lbl]) for lbl in cohort_labels
                    }
    else:
        return

    _attach_iteration_centroid_scope(
        iteration,
        layout=layout,
        base_project_path=base_project_path,
        run_dir=run_dir,
        cohort_labels=cohort_labels,
        train_by_label=train_by_label,
        parallel_seed=parallel_seed,
        full_cohort_by_label=full_cohort_by_label,
        seed_dir_by_label=seed_dir_by_label,
        previous_train_by_label=previous_train_by_label,
    )


def _comparison_labels(base_project: Path, request: ValidationPlanRequest) -> List[str]:
    if request.orderedComparisonLabels:
        return list(request.orderedComparisonLabels)
    with open(base_project, encoding="utf-8") as f:
        raw = json.load(f)
    comparisons = raw.get("comparisons") or []
    labels = [str(c.get("label") or c.get("name") or "") for c in comparisons if isinstance(c, dict)]
    labels = [x for x in labels if x]
    if labels:
        return labels
    return ["validation"]


def _materialize_iteration(
    *,
    config: MonteCarloConfig,
    base_project: Path,
    monte_carlo_runs_root: Path,
    layout: str,
    cohort_paths_list: List[Tuple[str, List[str]]],
    cohort_labels: List[str],
    full_cohort_by_label: Dict[str, List[str]],
    seed_dir_by_label: Dict[str, str],
    parallel_seed: bool,
    phase: str,
    phase_index: int,
    global_run_number: int,
    seed_offset: int,
    previous_train_control: Optional[List[str]],
    previous_train_disease: Optional[List[str]],
    previous_train_by_label: Optional[Dict[str, List[str]]],
    previous_run_dir: Optional[Path],
    overwrite: bool,
) -> Tuple[
    ValidationPlannedIteration,
    Optional[List[str]],
    Optional[List[str]],
    Optional[Dict[str, List[str]]],
    Optional[Path],
]:
    run_dir_name = f"run_{global_run_number:04d}"
    run_dir = monte_carlo_runs_root / run_dir_name
    run_dir.mkdir(parents=True, exist_ok=True)
    display_run_id = f"{phase}_run_{phase_index:04d}"
    seed_i = (int(config.seed) + seed_offset) if config.seed is not None else None

    control_paths = cohort_paths_list[0][1] if layout == "binary" else []
    disease_paths = cohort_paths_list[1][1] if layout == "binary" else []

    if layout == "binary":
        train_control, train_disease, val_control, val_disease = stratified_split(
            control_paths, disease_paths, config.train_fraction, seed=seed_i
        )
    else:
        train_m, val_m = stratified_split_multiclass(
            cohort_paths_list, config.train_fraction, seed=seed_i
        )

    project_path = run_dir / "project.json"
    if project_path.is_file() and not overwrite and not run_project_needs_regeneration(project_path):
        detector_path = resolve_detector_step_override_path(run_dir, config)
        task_config = _build_task_config(
            display_run_id=display_run_id,
            phase=phase,
            phase_index=phase_index,
            layout=layout,
            config=config,
            seed_i=seed_i,
            project_path=project_path,
            run_dir=run_dir,
            monte_carlo_runs_root=monte_carlo_runs_root,
            detector_step_override_path=detector_path,
        )
        iteration = ValidationPlannedIteration(
            runId=display_run_id,
            phase=phase,
            projectPath=_product_path_str(project_path),
            runDir=_product_path_str(run_dir),
            taskConfig=task_config,
        )
        _rehydrate_iteration_centroid_scope(
            iteration,
            layout=layout,
            run_dir=run_dir,
            base_project_path=base_project,
            cohort_paths_list=cohort_paths_list,
            cohort_labels=cohort_labels,
            samples_base_path=config.samples_base_path,
            parallel_seed=parallel_seed,
            full_cohort_by_label=full_cohort_by_label,
            seed_dir_by_label=seed_dir_by_label,
            previous_run_dir=previous_run_dir if not parallel_seed else None,
        )
        if not parallel_seed and previous_run_dir is not None:
            iteration.previousRunDir = _product_path_str(previous_run_dir)
        return (
            _tag_iteration_as_stratified_draw(
                iteration,
                run_dir=run_dir,
                layout=layout,
                cohort_labels=cohort_labels,
                config=config,
                seed=seed_i,
            ),
            previous_train_control,
            previous_train_disease,
            previous_train_by_label,
            previous_run_dir if not parallel_seed else None,
        )

    det_override_path = write_detector_featurecuts_override(run_dir, config)
    detector_path = (
        _product_path_str(det_override_path) if det_override_path is not None else None
    )
    if config.stability_gene_featurecuts_enabled:
        write_mapper_classifier_override(run_dir, config)
    if config.stability_gene_biomarker_filter_enabled and not config.stability_mapper_enrich_disease:
        import warnings

        warnings.warn(
            "stability_gene_biomarker_filter_enabled with stability_mapper_enrich_disease=false "
            "may yield empty pools when enricher.disease_only=true (mapper disease columns missing).",
            stacklevel=2,
        )

    val_control_csv = None
    val_disease_csv = None
    val_groups_json = None
    centroid_overrides_by_label: Dict[str, Path] = {}
    c1 = None
    c2 = None

    if layout == "binary":
        (
            project_path,
            _t1,
            _t2,
            val_control_csv,
            val_disease_csv,
            c1,
            c2,
        ) = generate_run_project(
            base_project,
            run_dir,
            run_dir_name,
            str(monte_carlo_runs_root),
            train_control,
            train_disease,
            val_control,
            val_disease,
            config.samples_base_path,
            previous_train_control_paths=previous_train_control if not parallel_seed else None,
            previous_train_disease_paths=previous_train_disease if not parallel_seed else None,
        )
        previous_train_control = list(train_control)
        previous_train_disease = list(train_disease)
        if not parallel_seed:
            prepare_incremental_centroid_baseline(previous_run_dir, run_dir, c1, c2)
    elif layout == "multiclass":
        project_path, val_groups_json = generate_run_project_multiclass(
            base_project,
            run_dir,
            run_dir_name,
            str(monte_carlo_runs_root),
            train_m,
            val_m,
            cohort_labels,
            config.samples_base_path,
        )
    else:
        project_path, val_groups_json, centroid_overrides_by_label = generate_run_project_hierarchical_multiclass(
            base_project,
            run_dir,
            run_dir_name,
            str(monte_carlo_runs_root),
            train_m,
            val_m,
            cohort_labels,
            config.samples_base_path,
            previous_train_by_label=previous_train_by_label if not parallel_seed else None,
        )

    if not parallel_seed and layout in {"multiclass", "hierarchical_multiclass"}:
        needs_baseline = any(
            centroid_override_has_remove_samples(path) for path in centroid_overrides_by_label.values()
        )
        if needs_baseline and previous_run_dir is not None:
            carry_forward_centroids_from_previous_run(previous_run_dir, run_dir)

    task_config = _build_task_config(
        display_run_id=display_run_id,
        phase=phase,
        phase_index=phase_index,
        layout=layout,
        config=config,
        seed_i=seed_i,
        project_path=project_path,
        run_dir=run_dir,
        monte_carlo_runs_root=monte_carlo_runs_root,
        detector_step_override_path=detector_path,
        val_control_csv=val_control_csv,
        val_disease_csv=val_disease_csv,
        val_groups_json=val_groups_json,
        c1=c1,
        c2=c2,
        centroid_overrides_by_label=centroid_overrides_by_label or None,
    )

    iteration = ValidationPlannedIteration(
        runId=display_run_id,
        phase=phase,
        projectPath=_product_path_str(project_path),
        runDir=_product_path_str(run_dir),
        taskConfig=task_config,
    )
    train_by_label_for_scope: Optional[Dict[str, List[str]]] = None
    previous_train_by_label_for_scope: Optional[Dict[str, List[str]]] = None
    if layout == "binary":
        train_by_label_for_scope = {
            cohort_labels[0]: list(train_control),
            cohort_labels[1]: list(train_disease),
        }
        if not parallel_seed and (previous_train_control is not None or previous_train_disease is not None):
            previous_train_by_label_for_scope = {
                cohort_labels[0]: list(previous_train_control or []),
                cohort_labels[1]: list(previous_train_disease or []),
            }
    elif layout in {"multiclass", "hierarchical_multiclass"}:
        train_by_label_for_scope = {lbl: list(train_m[lbl]) for lbl in cohort_labels}
        previous_train_by_label_for_scope = previous_train_by_label if not parallel_seed else None
        if not parallel_seed:
            previous_train_by_label = {lbl: list(train_m[lbl]) for lbl in cohort_labels}

    if train_by_label_for_scope is not None:
        _attach_iteration_centroid_scope(
            iteration,
            layout=layout,
            base_project_path=base_project,
            run_dir=run_dir,
            cohort_labels=cohort_labels,
            train_by_label=train_by_label_for_scope,
            parallel_seed=parallel_seed,
            full_cohort_by_label=full_cohort_by_label,
            seed_dir_by_label=seed_dir_by_label,
            previous_train_by_label=previous_train_by_label_for_scope,
        )
    if not parallel_seed and previous_run_dir is not None:
        iteration.previousRunDir = _product_path_str(previous_run_dir)

    return (
        _tag_iteration_as_stratified_draw(
            iteration,
            run_dir=run_dir,
            layout=layout,
            cohort_labels=cohort_labels,
            config=config,
            seed=seed_i,
        ),
        previous_train_control,
        previous_train_disease,
        previous_train_by_label if layout in {"multiclass", "hierarchical_multiclass"} and not parallel_seed else None,
        run_dir if not parallel_seed else None,
    )


def plan_validation_context(
    request: ValidationPlanRequest | Mapping[str, Any],
    *,
    profile_overrides: Optional[ValidationStepConfig] = None,
) -> ValidationPlanContext:
    """
    Materialize Monte Carlo run directories and return ValidationPipeline ``context_json``.

    Writes per-run ``project.json`` + train/val CSVs under ``output_base/project_name/monte_carlo_runs/``.
    """
    resolved_profile = profile_overrides
    if isinstance(request, Mapping):
        raw = dict(request)
        if resolved_profile is None:
            resolved_profile = parse_validation_profile(raw.get("resolvedConfig"))
        request = ValidationPlanRequest.model_validate(
            {key: value for key, value in raw.items() if key != "resolvedConfig"}
        )
    else:
        request = ValidationPlanRequest.model_validate(request)

    base_project = resolve_base_project_json(request.projectPath)
    config = _load_config_from_project(
        base_project, request, profile_overrides=resolved_profile
    )
    monte_carlo_runs_root, project_root = _monte_carlo_runs_root(config, base_project)
    parallel_seed = _parallel_mc_centroid_seed_enabled(config)

    write_mc_config_snapshot(config, mc_config_snapshot_path(monte_carlo_runs_root))

    layout = request.layout or infer_monte_carlo_layout(base_project, len(config.cohorts))
    cohort_paths_list: List[Tuple[str, List[str]]] = []
    for cohort in config.cohorts:
        paths = load_and_resolve_sample_paths(cohort.csv, config.samples_base_path)
        if not paths:
            raise ValueError(f"Cohort {cohort.label!r} ({cohort.csv}) has no samples")
        cohort_paths_list.append((cohort.label, paths))
    from .holdout_eval import filter_cohort_paths_excluding, holdout_basenames_from_config

    holdout_basenames = holdout_basenames_from_config(config)
    if holdout_basenames:
        cohort_paths_list, removed_holdout = filter_cohort_paths_excluding(
            cohort_paths_list,
            holdout_basenames,
        )
        missing = sorted(holdout_basenames - set(removed_holdout))
        if missing:
            raise ValueError(
                "Configured locked hold-out sample(s) were not found in the study cohorts: "
                f"{missing[:10]}"
            )
        empty_labels = [label for label, paths in cohort_paths_list if not paths]
        if empty_labels:
            raise ValueError(
                "Locked hold-out exclusion left empty cohort(s): "
                f"{empty_labels}. Reduce or correct validation_partitions."
            )
    cohort_labels = [c.label for c in config.cohorts]
    full_cohort_by_label = {label: list(paths) for label, paths in cohort_paths_list}

    centroid_seed_groups = (
        build_centroid_seed_groups(
            monte_carlo_runs_root=monte_carlo_runs_root,
            base_project_path=base_project,
            cohort_paths_list=cohort_paths_list,
        )
        if parallel_seed
        else []
    )
    seed_dir_by_label = {g.label: g.centroidDir for g in centroid_seed_groups}

    feature_n = request.featureIterations if request.featureIterations is not None else config.n_iterations
    quality_n = request.qualityIterations if request.qualityIterations is not None else 0

    iterations: List[ValidationPlannedIteration] = []
    previous_train_control: Optional[List[str]] = None
    previous_train_disease: Optional[List[str]] = None
    previous_train_by_label: Optional[Dict[str, List[str]]] = None
    previous_run_dir: Optional[Path] = None
    global_run = 0
    seed_offset = 0

    for phase, count in (("feature", feature_n), ("quality", quality_n)):
        if count <= 0:
            continue
        for phase_index in range(1, count + 1):
            global_run += 1
            (
                iteration,
                previous_train_control,
                previous_train_disease,
                previous_train_by_label,
                previous_run_dir,
            ) = _materialize_iteration(
                config=config,
                base_project=base_project,
                monte_carlo_runs_root=monte_carlo_runs_root,
                layout=layout,
                cohort_paths_list=cohort_paths_list,
                cohort_labels=cohort_labels,
                full_cohort_by_label=full_cohort_by_label,
                seed_dir_by_label=seed_dir_by_label,
                parallel_seed=parallel_seed,
                phase=phase,
                phase_index=phase_index,
                global_run_number=global_run,
                seed_offset=seed_offset,
                previous_train_control=previous_train_control,
                previous_train_disease=previous_train_disease,
                previous_train_by_label=previous_train_by_label,
                previous_run_dir=previous_run_dir,
                overwrite=request.overwrite,
            )
            iterations.append(iteration)
            seed_offset += 1

    if not iterations:
        raise ValueError("No iterations planned (featureIterations and qualityIterations are both zero)")

    plan_summary = ValidationPlanSummary(
        baseProject=str(base_project),
        layout=layout,
        featureIterations=feature_n,
        qualityIterations=quality_n,
        seed=config.seed,
        trainFraction=config.train_fraction,
        monteCarloRunsRoot=str(monte_carlo_runs_root.resolve()),
        parallelMcCentroidSeed=parallel_seed,
        iterations=iterations,
    )

    return ValidationPlanContext(
        projectPath=project_root,
        workerToolMapper=request.workerToolMapper,
        workerToolEnricher=request.workerToolEnricher,
        workerToolProgression=request.workerToolProgression,
        orderedComparisonLabels=_comparison_labels(base_project, request),
        centroidSeedGroups=centroid_seed_groups,
        iterations=iterations,
        validationPlan=plan_summary,
    )
