"""
Pre-generate per-run project.json and train/val lists for distributed discovery tasks.
"""

from __future__ import annotations

import json
import re
import sys
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .config import MonteCarloConfig
from .mc_config_load import write_mc_config_snapshot
from .mc_manifest import (
    write_baseline_manifest,
    write_detector_featurecuts_override,
    write_mapper_classifier_override,
)
from .project_gen import (
    apply_frozen_pipeline_artifacts_to_run_project,
    generate_run_project,
    generate_run_project_hierarchical_multiclass,
    generate_run_project_multiclass,
    infer_monte_carlo_layout,
)
from .split import load_and_resolve_sample_paths, stratified_split, stratified_split_multiclass
from .storage_layout import (
    atomic_write_json,
    claims_subdir,
    mc_config_snapshot_path,
    plan_runs_path,
    queue_subdir,
    run_task_path,
    tasks_subdir,
)
from .task_schema import DiscoveryRunTaskV1, parse_discovery_task_file, TaskMode

__all__ = [
    "plan_discovery_runs",
]


def _queue_status_completed_for_preserve(run_dir: Path) -> bool:
    p = run_dir / "queue_task_status.json"
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(data.get("status")) == "completed"


def _ensure_queue_dirs(monte_carlo_runs_root: Path) -> None:
    q = queue_subdir(monte_carlo_runs_root)
    q.mkdir(parents=True, exist_ok=True)
    tasks_subdir(monte_carlo_runs_root).mkdir(parents=True, exist_ok=True)
    claims_subdir(monte_carlo_runs_root).mkdir(parents=True, exist_ok=True)


def plan_discovery_runs(
    *,
    config: MonteCarloConfig,
    base_project: Path,
    monte_carlo_runs_root: Path,
    overwrite: bool = False,
    wipe_runs: bool = False,
) -> Dict[str, Any]:
    """
    Write ``run_####/project.json`` and training/validation CSVs for each MC iteration, plus
    ``queue/mc_config.json``, ``queue/tasks/``, and ``queue/plan_runs.json``.

    Does **not** run centroid/detector (use ``run-task`` on each worker). ``--skip-centroid`` is
    not supported: workers require pre-generated run directories from this planner with full splits.

    * ``--overwrite`` replaces ``queue/tasks`` and regenerates the plan; it refreshes per-run
      inputs under each ``run_####`` but does **not** delete existing ``run_####`` trees (so
      completed worker outputs are preserved by default).
    * ``--wipe-runs`` (optional) removes all existing ``run_####`` directories and ``queue/tasks``
      before planning — same as the legacy ``--overwrite`` behavior, and irreversibly deletes
      prior per-run pipeline outputs.

    * **Incremental replans** (no ``--overwrite`` / ``--wipe-runs``): any run with
      ``queue_task_status.json`` status ``completed`` and an existing ``queue/tasks/<run_id>.json``
      keeps its on-disk ``project.json`` and training/validation lists; only the task is refreshed
      (e.g. ``mc_config`` path) and new iterations are materialized. If you change ``seed``,
      ``train_fraction``, or cohorts, use ``--overwrite`` so every planned run is rewritten
      consistently. If ``queue/tasks`` was deleted, completed runs are re-materialized (project
      files rewritten) because the task JSON must be rebuilt.
    """
    if config.predictor_only and not config.frozen_project_path:
        default_frozen = monte_carlo_runs_root / "production" / "project.json"
        if default_frozen.is_file():
            config = config.model_copy(update={"frozen_project_path": str(default_frozen)})
    if config.predictor_only and not (config.frozen_project_path and Path(str(config.frozen_project_path)).is_file()):
        print(
            "Error: plan-runs in predictor-only mode needs frozen_project_path in config or "
            f"an existing {monte_carlo_runs_root / 'production' / 'project.json'}.",
            file=sys.stderr,
        )
        sys.exit(1)

    layout = infer_monte_carlo_layout(base_project, len(config.cohorts))
    cohort_paths_list: List[Tuple[str, List[str]]] = []
    for c in config.cohorts:
        paths = load_and_resolve_sample_paths(c.csv, config.samples_base_path)
        if not paths:
            print(
                f"Error: cohort {c.label!r} ({c.csv}) must list at least one sample.",
                file=sys.stderr,
            )
            sys.exit(1)
        cohort_paths_list.append((c.label, paths))

    cohort_labels = [c.label for c in config.cohorts]
    control_paths: List[str] = []
    disease_paths: List[str] = []
    if layout == "binary":
        control_paths = cohort_paths_list[0][1]
        disease_paths = cohort_paths_list[1][1]

    per_cancer_group = False
    if layout == "hierarchical_multiclass":
        per_cancer_group = True

    run_id_re = re.compile(r"^run_(\d{4})$")
    if monte_carlo_runs_root.is_dir():
        if wipe_runs:
            for name in list(monte_carlo_runs_root.iterdir()):
                if name.is_dir() and run_id_re.match(name.name):
                    shutil.rmtree(name, ignore_errors=True)
            tdir = tasks_subdir(monte_carlo_runs_root)
            if tdir.is_dir():
                shutil.rmtree(tdir, ignore_errors=True)
        elif overwrite:
            tdir = tasks_subdir(monte_carlo_runs_root)
            if tdir.is_dir():
                shutil.rmtree(tdir, ignore_errors=True)

    write_baseline_manifest(
        output_root=monte_carlo_runs_root,
        mode="monte_carlo_queue",
        config=config,
        layout=layout,
        cohort_paths_list=cohort_paths_list,
    )
    mc_path = mc_config_snapshot_path(monte_carlo_runs_root)
    write_mc_config_snapshot(config, mc_path)
    _ensure_queue_dirs(monte_carlo_runs_root)

    previous_train_control: List[str] | None = None
    previous_train_disease: List[str] | None = None
    run_records: List[Dict[str, Any]] = []
    n_preserved = 0
    n_fresh = 0

    for i in range(config.n_iterations):
        run_id = f"run_{i + 1:04d}"
        run_dir = monte_carlo_runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        seed_i = (int(config.seed) + i) if config.seed is not None else None
        tpath = run_task_path(monte_carlo_runs_root, run_id)
        if layout == "binary":
            try:
                train_control, train_disease, val_control, val_disease = stratified_split(
                    control_paths, disease_paths, config.train_fraction, seed=seed_i
                )
            except ValueError as e:
                print(
                    f"Warning: iteration {i + 1} skipped in plan: {e}",
                    file=sys.stderr,
                )
                continue
        elif layout in ("multiclass", "hierarchical_multiclass"):
            try:
                train_m, val_m = stratified_split_multiclass(
                    cohort_paths_list, config.train_fraction, seed=seed_i
                )
            except ValueError as e:
                print(f"Warning: iteration {i + 1} skipped in plan: {e}", file=sys.stderr)
                continue
        else:
            print(f"Error: unknown layout {layout}", file=sys.stderr)
            sys.exit(1)

        want_preserve = (
            (not overwrite)
            and (not wipe_runs)
            and (run_dir / "project.json").is_file()
            and tpath.is_file()
            and _queue_status_completed_for_preserve(run_dir)
        )
        old_task: Optional[DiscoveryRunTaskV1] = None
        if want_preserve:
            try:
                old_task = parse_discovery_task_file(str(tpath))
            except (OSError, json.JSONDecodeError, ValidationError, TypeError) as e:
                print(
                    f"Warning: could not read {tpath} ({e}); re-materializing {run_id}.",
                    file=sys.stderr,
                )
                want_preserve = False

        det_override_path: Optional[Path] = None
        val_control_csv: Optional[Path] = None
        val_disease_csv: Optional[Path] = None
        val_groups_json: Optional[Path] = None
        c1: Optional[Path] = None
        c2: Optional[Path] = None
        project_path: Path
        n_train: int
        n_val: int
        task: DiscoveryRunTaskV1

        if want_preserve and old_task is not None:
            n_preserved += 1
            if layout == "binary":
                previous_train_control = list(train_control)
                previous_train_disease = list(train_disease)
                n_train = len(train_control) + len(train_disease)
                n_val = len(val_control) + len(val_disease)
            else:
                n_train = sum(len(train_m[k]) for k in cohort_labels)
                n_val = sum(len(val_m[k]) for k in cohort_labels)
            task = old_task.model_copy(update={"mc_config_path": str(mc_path.resolve())})
            project_path = Path(str(task.project_json))
        else:
            n_fresh += 1
            det_override_path = write_detector_featurecuts_override(run_dir, config)
            if config.stability_gene_featurecuts_enabled:
                write_mapper_classifier_override(run_dir, config)
            if config.stability_gene_biomarker_filter_enabled and not config.stability_mapper_enrich_disease:
                import warnings

                warnings.warn(
                    "stability_gene_biomarker_filter_enabled with stability_mapper_enrich_disease=false "
                    "may yield empty pools when enricher.disease_only=true.",
                    stacklevel=2,
                )

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
                    run_id,
                    str(monte_carlo_runs_root),
                    train_control,
                    train_disease,
                    val_control,
                    val_disease,
                    config.samples_base_path,
                    previous_train_control_paths=previous_train_control,
                    previous_train_disease_paths=previous_train_disease,
                )
                previous_train_control = list(train_control)
                previous_train_disease = list(train_disease)
                if config.predictor_only:
                    apply_frozen_pipeline_artifacts_to_run_project(
                        project_path, Path(str(config.frozen_project_path))
                    )
                n_train = len(train_control) + len(train_disease)
                n_val = len(val_control) + len(val_disease)
            elif layout == "multiclass":
                project_path, val_groups_json = generate_run_project_multiclass(
                    base_project,
                    run_dir,
                    run_id,
                    str(monte_carlo_runs_root),
                    train_m,
                    val_m,
                    cohort_labels,
                    config.samples_base_path,
                )
                if config.predictor_only:
                    apply_frozen_pipeline_artifacts_to_run_project(
                        project_path, Path(str(config.frozen_project_path))
                    )
                n_train = sum(len(train_m[k]) for k in cohort_labels)
                n_val = sum(len(val_m[k]) for k in cohort_labels)
            else:  # hierarchical
                project_path, val_groups_json, _centroid_overrides = generate_run_project_hierarchical_multiclass(
                    base_project,
                    run_dir,
                    run_id,
                    str(monte_carlo_runs_root),
                    train_m,
                    val_m,
                    cohort_labels,
                    config.samples_base_path,
                )
                if config.predictor_only:
                    apply_frozen_pipeline_artifacts_to_run_project(
                        project_path, Path(str(config.frozen_project_path))
                    )
                n_train = sum(len(train_m[k]) for k in cohort_labels)
                n_val = sum(len(val_m[k]) for k in cohort_labels)

            task = DiscoveryRunTaskV1(
                task_id=f"discovery_{run_id}",
                mode=TaskMode.PREDICTOR_ONLY if config.predictor_only else TaskMode.DISCOVERY,
                run_id=run_id,
                iteration=i,
                layout=layout,  # type: ignore[arg-type]
                project_json=str(project_path),
                monte_carlo_runs_root=str(monte_carlo_runs_root.resolve()),
                run_dir=str(run_dir.resolve()),
                skip_centroid=False,
                per_cancer_group=per_cancer_group,
                predictor_only=bool(config.predictor_only),
                frozen_project_path=str(config.frozen_project_path) if config.frozen_project_path else None,
                val_control_csv=str(val_control_csv) if val_control_csv is not None else None,
                val_disease_csv=str(val_disease_csv) if val_disease_csv is not None else None,
                val_groups_json=str(val_groups_json) if val_groups_json is not None else None,
                centroid_group1_override=str(c1) if c1 is not None else None,
                centroid_group2_override=str(c2) if c2 is not None else None,
                detector_step_override_path=(
                    str(det_override_path.resolve()) if det_override_path is not None else None
                ),
                mc_config_path=str(mc_path.resolve()),
            )
        tpath.write_text(task.model_dump_json(indent=2), encoding="utf-8")
        run_records.append(
            {
                "run_id": run_id,
                "iteration": i + 1,
                "task_json": str(tpath),
                "project_json": str(project_path),
                "run_dir": str(run_dir.resolve()),
                "n_train_samples": n_train,
                "n_val_samples": n_val,
            }
        )

    if not run_records:
        print("Error: no runs planned (all iterations skipped?)", file=sys.stderr)
        sys.exit(1)

    plan_payload: Dict[str, Any] = {
        "queue_schema_version": "1.0",
        "mode": "discovery_queue",
        "n_planned": len(run_records),
        "monte_carlo_runs_root": str(monte_carlo_runs_root.resolve()),
        "base_project": str(base_project),
        "layout": layout,
        "mc_config": str(mc_path),
        "runs": run_records,
    }
    atomic_write_json(plan_runs_path(monte_carlo_runs_root), plan_payload, indent=2)
    return {
        **plan_payload,
        "n_preserved": n_preserved,
        "n_materialized": n_fresh,
    }
