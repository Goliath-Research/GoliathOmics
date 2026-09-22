"""Workflow-facing model MC orchestration (CLI --model-mc --model-mc-all parity)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from methyl_validation.config import MonteCarloConfig
from methyl_validation.project_gen import infer_monte_carlo_layout
from methyl_validation.split import load_and_resolve_sample_paths
from methyl_validation.mc_manifest import write_baseline_manifest


def _exclude_configured_holdout(
    cohort_paths_list: List[Tuple[str, List[str]]],
    config: MonteCarloConfig,
) -> List[Tuple[str, List[str]]]:
    """Keep model-MC pools aligned with the primary MC development cohort."""
    if not bool(getattr(config, "holdout_exclude_from_training", True)):
        return cohort_paths_list
    partitions = getattr(config, "validation_partitions", None)
    partition_name = getattr(config, "holdout_partition", "locked_test")
    holdout_paths = list(getattr(partitions, partition_name, []) or []) if partitions else []
    if not holdout_paths:
        return cohort_paths_list
    holdout_ids = {Path(path).name for path in holdout_paths}
    return [
        (label, [path for path in paths if Path(path).name not in holdout_ids])
        for label, paths in cohort_paths_list
    ]


def resolve_model_mc_backends(config: MonteCarloConfig, *, run_all: bool) -> List[str]:
    from methyl_validation.cli import _resolve_model_mc_backends

    return _resolve_model_mc_backends(config, run_all)


def _requires_detector_classifier_models(
    config: MonteCarloConfig,
    configured_backends: Sequence[str],
) -> bool:
    """Return whether a configured backend consumes detector classifier pickles."""
    if "ecdf" not in configured_backends:
        return False
    ecdf_config = config.with_backend_selection("ecdf")
    return (
        ecdf_config.feature_mode != "raw_gene"
        and not bool(ecdf_config.ecdf_aggregated_enabled)
    )


def run_model_mc_all(
    *,
    production_project: Path,
    monte_carlo_runs_root: Path,
    config: MonteCarloConfig,
    backends: Optional[Sequence[str]] = None,
    resume: Optional[int] = None,
    require_artifact_reuse: bool = False,
) -> Dict[str, Any]:
    """Run shared model-mc iterations then per-backend model training loops."""
    from methyl_validation.cli import (
        _build_model_mc_shared_runs,
        _run_model_mc_backend_from_shared_runs,
    )

    if not production_project.is_file():
        raise FileNotFoundError(f"production project not found: {production_project}")

    layout = infer_monte_carlo_layout(production_project, len(config.cohorts))
    cohort_paths_list: List[Tuple[str, List[str]]] = []
    for cohort in config.cohorts:
        paths = load_and_resolve_sample_paths(cohort.csv, config.samples_base_path)
        if not paths:
            raise ValueError(f"cohort {cohort.label!r} ({cohort.csv}) must list at least one sample")
        cohort_paths_list.append((cohort.label, paths))
    cohort_paths_list = _exclude_configured_holdout(cohort_paths_list, config)
    for label, paths in cohort_paths_list:
        if not paths:
            raise ValueError(f"cohort {label!r} is empty after holdout exclusion")
    cohort_labels = [c.label for c in config.cohorts]
    control_paths: List[str] = []
    disease_paths: List[str] = []
    if layout == "binary":
        control_paths = cohort_paths_list[0][1]
        disease_paths = cohort_paths_list[1][1]

    model_mc_root = monte_carlo_runs_root / "model_mc"
    model_mc_root.mkdir(parents=True, exist_ok=True)
    write_baseline_manifest(
        output_root=model_mc_root,
        mode="model_mc",
        config=config,
        layout=layout,
        cohort_paths_list=cohort_paths_list,
        split_reuse_source_root=monte_carlo_runs_root,
    )

    configured = list(backends) if backends else resolve_model_mc_backends(config, run_all=True)
    shared_root = model_mc_root / "shared"
    shared_rows = _build_model_mc_shared_runs(
        base_project_for_runs=production_project,
        config=config,
        layout=layout,
        cohort_paths_list=cohort_paths_list,
        cohort_labels=cohort_labels,
        control_paths=control_paths,
        disease_paths=disease_paths,
        shared_root=shared_root,
        resume_arg=resume,
        per_cancer_group=False,
        primary_monte_carlo_runs_root=monte_carlo_runs_root,
        require_artifact_reuse=require_artifact_reuse,
        require_classifier_models=_requires_detector_classifier_models(config, configured),
    )
    backend_roots: Dict[str, str] = {}
    for backend in configured:
        backend_root = model_mc_root / backend
        _run_model_mc_backend_from_shared_runs(
            backend=backend,
            config=config.with_backend_selection(backend),
            layout=layout,
            cohort_paths_list=cohort_paths_list,
            backend_root=backend_root,
            shared_root=shared_root,
            shared_rows=shared_rows,
            resume_arg=resume,
            per_cancer_group=False,
            primary_monte_carlo_runs_root=monte_carlo_runs_root,
        )
        backend_roots[backend] = str(backend_root)

    ranking_csv = model_mc_root / "backend_ranking.csv"
    ranking_json = model_mc_root / "backend_ranking.json"
    return {
        "status": "ok",
        "modelMcRoot": str(model_mc_root),
        "sharedRoot": str(shared_root),
        "backends": configured,
        "backendRoots": backend_roots,
        "backendRankingCsv": str(ranking_csv) if ranking_csv.is_file() else None,
        "backendRankingJson": str(ranking_json) if ranking_json.is_file() else None,
        "nSharedIterations": len(shared_rows),
    }
