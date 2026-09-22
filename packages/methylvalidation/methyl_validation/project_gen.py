"""
Generate per-iteration project JSON and train/val CSVs for Monte Carlo runs.
"""

import copy
import csv
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from methyl_domain.types import CentroidGroupScope, CentroidSeedGroup


def prepare_path_for_write(path: Path) -> Path:
    """Ensure ``path`` can be opened for write without following a CAAS product symlink.

    Monte Carlo run files are often symlinks into ``.caas/validation_plan_iterations/<key>/``.
    Opening those for write would recreate blobs under the old content key (or fail if the
    target was deleted). Replace the leaf first so writers always create a real file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.is_file():
        path.unlink()
    return path


def write_run_project_json(project: Dict[str, Any], project_path: Path) -> None:
    """Write per-iteration ``project.json`` (slim manifest; no embedded ``step_config``)."""
    payload = copy.deepcopy(project)
    payload.pop("step_config", None)
    project_path = prepare_path_for_write(project_path)
    with open(project_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def run_project_needs_regeneration(project_path: Path) -> bool:
    """True when an on-disk MC run project must be rewritten (legacy or invalid)."""
    project_path = Path(project_path)
    if not project_path.is_file():
        return True
    try:
        with open(project_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(data, dict):
        return True
    return "step_config" in data


def monte_carlo_runs_have_legacy_projects(monte_carlo_runs_root: Path) -> bool:
    """True when any ``run_*/project.json`` under the MC root embeds legacy ``step_config``."""
    root = Path(monte_carlo_runs_root)
    if not root.is_dir():
        return False
    for project_path in sorted(root.glob("run_*/project.json")):
        if run_project_needs_regeneration(project_path):
            return True
    return False


def _sample_name_from_path(full_path: str, base_path: str) -> str:
    """Return sample folder name (last component) for train CSV format."""
    p = Path(full_path)
    base = Path(base_path).resolve()
    try:
        p_resolved = p.resolve()
        if base in p_resolved.parents or p_resolved == base:
            return str(p_resolved.relative_to(base))
    except ValueError:
        pass
    return p.name


def prepare_model_mc_backend_run_from_shared(
    shared_run_dir: str | Path,
    backend_run_dir: str | Path,
    *,
    backend_root: str | Path,
) -> Path:
    """
    Materialize a backend-local iteration tree so model artifacts land under
    ``backend_run_dir`` (not ``model_mc/shared``).

    Copies split CSVs and detector symlinks from the shared iteration, then writes
    ``project.json`` with ``output_base``/``project_name`` routed to the backend run.
    """
    shared_run_dir = Path(shared_run_dir)
    backend_run_dir = Path(backend_run_dir)
    backend_root = Path(backend_root)
    backend_run_dir.mkdir(parents=True, exist_ok=True)

    def _replace_path(dst: Path) -> None:
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        elif dst.is_dir():
            shutil.rmtree(dst)

    for name in (
        "train_control.csv",
        "train_disease.csv",
        "test_control.csv",
        "test_disease.csv",
        "test_groups.json",
        "centroid_group1_override.json",
        "centroid_group2_override.json",
    ):
        src = shared_run_dir / name
        dst = backend_run_dir / name
        if not src.is_file():
            continue
        if dst.exists():
            _replace_path(dst)
        shutil.copy2(src, dst)

    for link_name in ("centroids", "detections", "detector_step_override.json"):
        src = shared_run_dir / link_name
        dst = backend_run_dir / link_name
        if not src.exists():
            continue
        if dst.exists() or dst.is_symlink():
            _replace_path(dst)
        if src.is_dir():
            dst.symlink_to(src.resolve(), target_is_directory=True)
        else:
            dst.symlink_to(src.resolve())

    shared_project_path = shared_run_dir / "project.json"
    if not shared_project_path.is_file():
        raise FileNotFoundError(f"Missing shared project.json: {shared_project_path}")
    with open(shared_project_path, encoding="utf-8") as f:
        payload = json.load(f)

    # Model-MC reuses frozen production support artifacts for non-DMP feature
    # families. Materialize stable links beside the backend bundle because
    # package-level model builders do not re-resolve study/profile config.
    model_bundle_cfg = (
        (payload.get("actionConfig") or {}).get("model_bundle") or {}
        if isinstance(payload, dict)
        else {}
    )
    backend_bundle_dir = backend_run_dir / "model_bundle"
    for config_key in (
        "mapper_annotation_csv",
        "stability_gene_panel",
        "fixed_gene_panel",
        "fixed_gene_features",
    ):
        configured_path = model_bundle_cfg.get(config_key)
        if not configured_path:
            continue
        source = Path(str(configured_path))
        if not source.is_file():
            continue
        backend_bundle_dir.mkdir(parents=True, exist_ok=True)
        destination = backend_bundle_dir / source.name
        if destination.exists() or destination.is_symlink():
            _replace_path(destination)
        destination.symlink_to(source.resolve())

    payload["output_base"] = str(backend_root)
    payload["project_name"] = backend_run_dir.name
    payload.pop("step_config", None)
    backend_project_path = backend_run_dir / "project.json"
    with open(backend_project_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return backend_project_path


def apply_frozen_pipeline_artifacts_to_run_project(
    run_project_path: str | Path,
    frozen_project_path: str | Path,
) -> None:
    """
    After a normal MC ``project.json`` is written (train samples + predictor holdouts), copy
    pipeline step configs and output routing from the frozen production project so
    ``methyl-predictor`` resolves centroids/classifiers built by ``--freeze`` without retraining.
    """
    run_project_path = Path(run_project_path)
    frozen_project_path = Path(frozen_project_path)
    with open(run_project_path, encoding="utf-8") as f:
        run_p = json.load(f)
    with open(frozen_project_path, encoding="utf-8") as f:
        fr = json.load(f)
    fr_sc = fr.get("step_config") or {}
    sc = run_p.setdefault("step_config", {})
    for key in ("centroid", "detection", "classifier", "mapper", "enricher"):
        if key in fr_sc:
            sc[key] = copy.deepcopy(fr_sc[key])
    if "output_base" in fr:
        run_p["output_base"] = fr["output_base"]
    if "project_name" in fr:
        run_p["project_name"] = fr["project_name"]
    with open(run_project_path, "w", encoding="utf-8") as f:
        json.dump(run_p, f, indent=2)


def write_train_csv(path: Path, full_paths: List[str], base_path: str) -> None:
    """
    Write a train CSV with header ``sample`` and one column of folder names under ``samples_base_path``.

    Matches pipeline project cohort list files (e.g. ``configs/healthy.csv``): MethylCentroid /
    detector / classifier resolve names with the run ``project.json`` ``samples_base_path``.
    Holdout lists use :func:`write_val_csv` instead (absolute paths) — see its docstring.
    """
    path = prepare_path_for_write(path)
    names = [_sample_name_from_path(p, base_path) for p in full_paths]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sample"])
        for n in names:
            w.writerow([n])


def write_val_csv(path: Path, full_paths: List[str]) -> None:
    """
    Write a validation CSV with header ``path`` and one column of **absolute** directory paths.

    MethylPredictor (and ``--test-groups`` expansion) may run with a different cwd than the
    Monte Carlo subprocess; absolute paths avoid relying on ``samples_base_path`` alone.
    Training cohorts use :func:`write_train_csv` (sample names) so the generated ``project.json``
    matches the rest of the pipeline’s list-file convention.

    When copying a project to another machine or mount, use ``--path-remap`` on
    ``methyl-validation --freeze`` (or equivalent): production freeze remaps these paths in
    copied list files; see :func:`methyl_validation.path_remap.remap_cohort_list_files_in_project`.
    """
    path = prepare_path_for_write(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path"])
        for p in full_paths:
            w.writerow([str(Path(p).resolve())])


def _first_control_and_disease_labels(base: Dict[str, Any]) -> tuple[str, str]:
    """Get first control group label and first disease group label from base project."""
    control_label = "healthy"
    disease_label = "disease"
    if "controls" in base and base["controls"] and base["controls"].get("groups"):
        control_label = base["controls"]["groups"][0].get("label", control_label)
    elif "control" in base and base["control"] and base["control"].get("groups"):
        control_label = base["control"]["groups"][0].get("label", control_label)
    if "diseases" in base and base["diseases"] and base["diseases"].get("groups"):
        disease_label = base["diseases"]["groups"][0].get("label", disease_label)
    elif "disease" in base and base["disease"] and base["disease"].get("groups"):
        disease_label = base["disease"]["groups"][0].get("label", disease_label)
    return control_label, disease_label


def _control_disease_side_labels(base: Dict[str, Any]) -> tuple[str, str]:
    """Side labels from ``controls.label`` / ``diseases.label`` (not group labels)."""
    control_side = "healthy"
    disease_side = "disease"
    if isinstance(base.get("controls"), dict):
        control_side = base["controls"].get("label", control_side)
    elif isinstance(base.get("control"), dict):
        control_side = base["control"].get("label", control_side)
    if isinstance(base.get("diseases"), dict):
        disease_side = base["diseases"].get("label", disease_side)
    elif isinstance(base.get("disease"), dict):
        disease_side = base["disease"].get("label", disease_side)
    return control_side, disease_side


def link_run_artifacts_from_source(
    source_run_dir: Union[str, Path],
    target_run_dir: Union[str, Path],
    *,
    artifacts: Sequence[str] = ("centroids", "detections"),
) -> Dict[str, Any]:
    """Symlink selected artifacts from a prior MC run into a model-mc iteration dir."""
    src_root = Path(source_run_dir)
    dst_root = Path(target_run_dir)
    dst_root.mkdir(parents=True, exist_ok=True)
    linked: List[str] = []
    requested = tuple(artifacts) if artifacts else ("centroids", "detections")

    def _replace_path(dst: Path) -> None:
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        elif dst.is_dir():
            shutil.rmtree(dst)

    for artifact_dir in requested:
        src = src_root / artifact_dir
        dst = dst_root / artifact_dir
        if not src.is_dir():
            continue
        _replace_path(dst)
        dst.symlink_to(src.resolve(), target_is_directory=True)
        linked.append(artifact_dir)
    if "detections" in requested:
        for optional_file in ("detector_step_override.json",):
            srcf = src_root / optional_file
            dstf = dst_root / optional_file
            if srcf.is_file():
                _replace_path(dstf)
                dstf.symlink_to(srcf.resolve())
                linked.append(optional_file)
    return {"linked": linked, "sourceRunDir": str(src_root.resolve()), "targetRunDir": str(dst_root.resolve())}


def _seed_centroid_dir_for_group(
    monte_carlo_runs_root: Union[str, Path],
    base_project_path: Union[str, Path],
    side: str,
    label: str,
    *,
    project: Any = None,
) -> str:
    """Return shared seed centroid output dir for one cohort under ``_centroid_seed/``."""
    if project is None:
        from methyl_utils import load_project

        project = load_project(str(base_project_path))
    template = Path(project.get_centroid_dir(side, label))
    parts = template.parts
    if "centroids" in parts:
        rel = Path(*parts[parts.index("centroids") :])
        return str((Path(monte_carlo_runs_root) / "_centroid_seed" / rel).resolve())
    return str((Path(monte_carlo_runs_root) / "_centroid_seed" / label).resolve())


def _run_scoped_artifact_dir(
    run_dir: Union[str, Path],
    template_path: Union[str, Path],
    *,
    anchor: str,
    fallback_name: str,
) -> str:
    """Map a base-project artifact path onto ``run_dir`` preserving layout from ``anchor``."""
    template = Path(template_path)
    parts = template.parts
    if anchor in parts:
        rel = Path(*parts[parts.index(anchor) :])
        return str((Path(run_dir) / rel).resolve())
    return str((Path(run_dir) / anchor / fallback_name).resolve())


def run_centroid_dir_for_group(
    run_dir: Union[str, Path],
    base_project_path: Union[str, Path],
    side: str,
    label: str,
    *,
    project: Any = None,
) -> str:
    """Return per-run centroid output dir for one cohort (mirrors base project layout)."""
    if project is None:
        from methyl_utils import load_project

        project = load_project(str(base_project_path))
    return _run_scoped_artifact_dir(
        run_dir,
        project.get_centroid_dir(side, label),
        anchor="centroids",
        fallback_name=label,
    )


def run_detection_output_dir_for_comparison(
    run_dir: Union[str, Path],
    base_project_path: Union[str, Path],
    control_group: str,
    disease_group: str,
    *,
    project: Any = None,
) -> str:
    """Return per-run detection output dir for one comparison."""
    if project is None:
        from methyl_utils import load_project

        project = load_project(str(base_project_path))
    return _run_scoped_artifact_dir(
        run_dir,
        project.get_detection_output_dir(control_group, disease_group),
        anchor="detections",
        fallback_name=f"{control_group}_{disease_group}",
    )


def build_centroid_seed_groups(
    *,
    monte_carlo_runs_root: Union[str, Path],
    base_project_path: Union[str, Path],
    cohort_paths_list: List[Tuple[str, List[str]]],
) -> List[CentroidSeedGroup]:
    """Build one seed group per MC cohort with the full resolved sample pool."""
    from methyl_utils import load_project

    project = load_project(str(base_project_path))
    side_by_label = _side_by_label(project)
    seeds: List[CentroidSeedGroup] = []
    for label, paths in cohort_paths_list:
        side = side_by_label.get(label, "control")
        seeds.append(
            CentroidSeedGroup(
                label=label,
                addSamples=_normalize_sample_paths(paths),
                removeSamples=[],
                centroidDir=_seed_centroid_dir_for_group(
                    monte_carlo_runs_root,
                    base_project_path,
                    side,
                    label,
                    project=project,
                ),
            )
        )
    return seeds


def _side_by_label(project: Any) -> Dict[str, str]:
    with_side = project._get_resolved_groups_with_side(expand_subclusters=True)
    return {lbl: side for lbl, _, side in with_side}


def build_cohort_relative_centroid_scope(
    *,
    base_project_path: Union[str, Path],
    run_dir: Union[str, Path],
    cohort_labels: List[str],
    full_cohort_by_label: Dict[str, List[str]],
    train_by_label: Dict[str, List[str]],
    seed_dir_by_label: Dict[str, str],
    project: Any = None,
) -> List[CentroidGroupScope]:
    """Build per-iteration centroid deltas vs the full cohort (parallel MC path)."""
    if project is None:
        from methyl_utils import load_project

        project = load_project(str(base_project_path))
    side_by_label = _side_by_label(project)
    groups: List[CentroidGroupScope] = []
    for label in cohort_labels:
        full_set = set(_normalize_sample_paths(full_cohort_by_label[label]))
        train_set = set(_normalize_sample_paths(train_by_label[label]))
        remove_paths = sorted(full_set - train_set)
        side = side_by_label.get(label, "control")
        groups.append(
            CentroidGroupScope(
                label=label,
                addSamples=[],
                removeSamples=remove_paths,
                centroidDir=_run_scoped_artifact_dir(
                    run_dir,
                    project.get_centroid_dir(side, label),
                    anchor="centroids",
                    fallback_name=label,
                ),
                centroidSeedDir=seed_dir_by_label[label],
            )
        )
    return groups


def build_group_centroid_scope(
    *,
    base_project_path: Union[str, Path],
    run_dir: Union[str, Path],
    cohort_labels: List[str],
    train_by_label: Dict[str, List[str]],
    previous_train_by_label: Optional[Dict[str, List[str]]] = None,
    project: Any = None,
) -> List[CentroidGroupScope]:
    """Build per-group add/remove lists and centroid output dirs (legacy sequential MC)."""
    if project is None:
        from methyl_utils import load_project

        project = load_project(str(base_project_path))
    side_by_label = _side_by_label(project)
    groups: List[CentroidGroupScope] = []
    prev_map = previous_train_by_label or {}
    for label in cohort_labels:
        train_paths = list(train_by_label[label])
        override = _build_centroid_step_override(prev_map.get(label), train_paths)
        side = side_by_label.get(label, "control")
        groups.append(
            CentroidGroupScope(
                label=label,
                addSamples=override["base_config"]["add_samples"],
                removeSamples=override["base_config"]["remove_samples"],
                centroidDir=_run_scoped_artifact_dir(
                    run_dir,
                    project.get_centroid_dir(side, label),
                    anchor="centroids",
                    fallback_name=label,
                ),
            )
        )
    return groups


def _normalize_sample_paths(paths: List[str]) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for path in paths:
        resolved = str(Path(path).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)
    return normalized


def _build_centroid_step_override(
    previous_paths: Optional[List[str]],
    current_paths: List[str],
) -> Dict[str, Any]:
    prev = _normalize_sample_paths(previous_paths or [])
    curr = _normalize_sample_paths(current_paths)
    prev_set = set(prev)
    curr_set = set(curr)
    add_paths = [path for path in curr if path not in prev_set]
    remove_paths = [path for path in prev if path not in curr_set]
    return {
        "base_config": {
            "add_samples": add_paths,
            "remove_samples": remove_paths,
        }
    }


def centroid_override_has_remove_samples(override_path: Optional[Union[str, Path]]) -> bool:
    """True when a centroid step override requests remove_samples (incremental MC delta)."""
    if override_path is None:
        return False
    path = Path(override_path)
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    base_config = payload.get("base_config")
    if not isinstance(base_config, dict):
        return False
    remove_samples = base_config.get("remove_samples") or []
    return bool(remove_samples)


def incremental_centroid_update_requested(
    centroid_group1_override: Optional[Union[str, Path]],
    centroid_group2_override: Optional[Union[str, Path]],
) -> bool:
    return (
        centroid_override_has_remove_samples(centroid_group1_override)
        or centroid_override_has_remove_samples(centroid_group2_override)
    )


def carry_forward_centroids_from_previous_run(
    previous_run_dir: Union[str, Path],
    current_run_dir: Union[str, Path],
) -> bool:
    """
    Copy the previous iteration's centroids tree into the current run directory.

    MethylCentroid incremental updates read samples_used baseline from HDF5 files in the
    current run's centroid output dir; Monte Carlo iterations use isolated run_XXXX roots.
    """
    prev = Path(previous_run_dir)
    cur = Path(current_run_dir)
    src = prev / "centroids"
    dst = cur / "centroids"
    if not src.is_dir():
        return False
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink():
            dst.unlink()
        elif dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return True


def copy_centroid_seed_baseline(seed_dir: Union[str, Path], output_dir: Union[str, Path]) -> bool:
    """
    Copy a shared per-group seed centroid tree into a run iteration output dir.

    Returns ``False`` when the seed directory does not exist (no baseline to apply).
    Raises ``FileNotFoundError`` when the seed directory exists but contains **no**
    ``*.h5`` centroids: copying an empty tree silently yields ``start=0`` deltas and
    the downstream ``methyl-centroid`` run fails with "No valid sample files found".
    Failing here surfaces the real cause (empty seed) instead of a misleading error.
    """
    src = Path(seed_dir)
    dst = Path(output_dir)
    if not src.is_dir():
        return False
    if not any(src.rglob("*.h5")):
        raise FileNotFoundError(
            f"Centroid seed directory has no *.h5 centroids: {src}. "
            "The seed phase must build centroids before cohort-relative MC iterations copy them. "
            "Verify the centroid_seed FOREACH completed and wrote HDF5 to this path."
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink():
            dst.unlink()
        elif dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return True


def prepare_incremental_centroid_baseline(
    previous_run_dir: Optional[Union[str, Path]],
    current_run_dir: Union[str, Path],
    centroid_group1_override: Optional[Union[str, Path]],
    centroid_group2_override: Optional[Union[str, Path]],
) -> bool:
    """Copy prior-run centroids when MC centroid deltas require an on-disk baseline."""
    if previous_run_dir is None:
        return False
    if not incremental_centroid_update_requested(
        centroid_group1_override,
        centroid_group2_override,
    ):
        return False
    carried = carry_forward_centroids_from_previous_run(previous_run_dir, current_run_dir)
    if carried:
        print(
            f"[centroid-baseline] Copied centroids from {previous_run_dir} to "
            f"{Path(current_run_dir) / 'centroids'} for incremental update.",
            file=sys.stderr,
        )
    else:
        print(
            f"[centroid-baseline] Warning: incremental centroid deltas requested but no centroids "
            f"found under {previous_run_dir}; methyl-centroid may build from add_samples only.",
            file=sys.stderr,
        )
    return carried


def write_centroid_step_override(
    path: Path,
    previous_paths: Optional[List[str]],
    current_paths: List[str],
) -> Path:
    path = prepare_path_for_write(path)
    payload = _build_centroid_step_override(previous_paths, current_paths)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def generate_run_project(
    base_project_path: str | Path,
    run_dir: Path,
    run_id: str,
    output_base: str,
    train_control_paths: List[str],
    train_disease_paths: List[str],
    val_control_paths: List[str],
    val_disease_paths: List[str],
    samples_base_path: str,
    previous_train_control_paths: Optional[List[str]] = None,
    previous_train_disease_paths: Optional[List[str]] = None,
) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    """
    Load base project JSON, write train/val CSVs, and write the run's project.json.
    Overrides output_base, project_name, controls and diseases to single groups with train samples only.
    Called by methyl-validation with output_base = monte_carlo_runs_root (output_base/project_name/monte_carlo_runs)
    so the run's paths are monte_carlo_runs_root/run_id/centroids|detections|...

    Returns:
        (
            project_json_path,
            train_control_csv,
            train_disease_csv,
            test_control_csv,
            test_disease_csv,
            centroid_group1_override_json,
            centroid_group2_override_json,
        )
    """
    with open(base_project_path, encoding="utf-8") as f:
        base = json.load(f)

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    train_control_csv = run_dir / "train_control.csv"
    train_disease_csv = run_dir / "train_disease.csv"
    test_control_csv = run_dir / "test_control.csv"
    test_disease_csv = run_dir / "test_disease.csv"

    write_train_csv(train_control_csv, train_control_paths, samples_base_path)
    write_train_csv(train_disease_csv, train_disease_paths, samples_base_path)
    write_val_csv(test_control_csv, val_control_paths)
    write_val_csv(test_disease_csv, val_disease_paths)

    control_group, disease_group = _first_control_and_disease_labels(base)
    control_side, disease_side = _control_disease_side_labels(base)

    # Build project: same structure as pipeline expects (controls/diseases plural ok per pipeline normalizer)
    project = dict(base)
    project.pop("step_config", None)
    project["output_base"] = output_base.rstrip("/")
    project["project_name"] = run_id
    project["samples_base_path"] = samples_base_path
    project["controls"] = {
        "label": control_side,
        "groups": [
            {"label": control_group, "sample_paths": [str(train_control_csv)]}
        ],
    }
    project["diseases"] = {
        "label": disease_side,
        "groups": [
            {"label": disease_group, "sample_paths": [str(train_disease_csv)]}
        ],
    }
    project["comparisons"] = [
        {"control_group": control_group, "disease_group": disease_group}
    ]
    action_config = copy.deepcopy(project.get("actionConfig") or {})
    predictor_config = copy.deepcopy(action_config.get("predictor") or {})
    predictor_config.update(
        {
            "train_control_paths": list(train_control_paths),
            "train_disease_paths": list(train_disease_paths),
            "test_control_paths": list(val_control_paths),
            "test_disease_paths": list(val_disease_paths),
        }
    )
    action_config["predictor"] = predictor_config
    project["actionConfig"] = action_config

    _write_binary_test_groups_json(
        run_dir,
        val_control_paths,
        val_disease_paths,
        control_group,
        disease_group,
    )

    project_path = run_dir / "project.json"
    write_run_project_json(project, project_path)

    group1_override = write_centroid_step_override(
        run_dir / "centroid_group1_override.json",
        previous_train_control_paths,
        train_control_paths,
    )
    group2_override = write_centroid_step_override(
        run_dir / "centroid_group2_override.json",
        previous_train_disease_paths,
        train_disease_paths,
    )

    return (
        project_path,
        train_control_csv,
        train_disease_csv,
        test_control_csv,
        test_disease_csv,
        group1_override,
        group2_override,
    )


def _safe_cohort_filename_label(label: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", label.strip())
    return s or "cohort"


def infer_monte_carlo_layout(base_project_path: str | Path, n_cohorts: int) -> str:
    """
    Return ``\"binary\"`` for control/disease templates with exactly two MC cohorts (legacy),
    only when the project also resolves to exactly two centroid groups (no staged subcohorts).
    Return ``\"hierarchical_multiclass\"`` when the base project uses ``controls``/``diseases`` and
    the number of resolved centroid groups equals ``n_cohorts`` and ``n_cohorts`` ≥ 3.
    Return ``\"multiclass\"`` when the base project uses flat ``groups`` whose length matches ``n_cohorts``.
    """
    with open(base_project_path, encoding="utf-8") as f:
        raw = json.load(f)
    groups = raw.get("groups")
    if isinstance(groups, list) and len(groups) >= 2:
        if len(groups) != n_cohorts:
            raise ValueError(
                f"Base project flat groups count ({len(groups)}) must match Monte Carlo cohorts ({n_cohorts})."
            )
        return "multiclass"
    try:
        from methyl_utils import load_project

        proj = load_project(base_project_path)
        if proj.uses_control_disease():
            resolved = proj.get_resolved_groups()
            resolved_labels = [x[0] for x in resolved]
            if n_cohorts >= 3 and len(resolved) == n_cohorts:
                return "hierarchical_multiclass"
            if n_cohorts >= 3 and len(resolved) != n_cohorts:
                raise ValueError(
                    f"Monte Carlo cohorts count ({n_cohorts}) does not match control/disease resolved groups "
                    f"count ({len(resolved)}): {resolved_labels!r}. "
                    "Use one cohort per resolved leaf label in the same order."
                )
            if n_cohorts == 2 and len(resolved) > 2:
                raise ValueError(
                    f"Monte Carlo config has 2 cohorts (e.g. legacy healthy_csv + disease_csv) but "
                    f"base project resolves to {len(resolved)} centroid groups {resolved_labels!r}. "
                    "Use one `cohorts` entry per resolved leaf in that exact order (labels must match), "
                    "e.g. `all` plus `pca_pca1`..`pca_pca4` with separate CSVs — not one merged disease CSV."
                )
    except ValueError:
        raise
    except Exception as e:
        if n_cohorts >= 3:
            raise ValueError(
                "Unable to resolve hierarchical control/disease groups from base project. "
                f"Underlying error: {e}"
            ) from e
    if n_cohorts != 2:
        raise ValueError(
            "For control/disease projects: use exactly two Monte Carlo cohorts (binary), or "
            "K cohorts matching K resolved groups (multiclass hierarchical, K>=3), "
            "or a flat ``groups`` base project."
        )
    return "binary"


def _assert_flat_multiclass_template(base: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ensure base JSON is a flat ``groups`` template (no control/disease). Return groups list."""
    for key in ("control", "disease", "controls", "diseases"):
        if base.get(key) is not None:
            raise ValueError(
                "Multiclass Monte Carlo requires a base project with top-level ``groups`` only "
                f"(found {key!r}). For two-cohort control/disease templates, use the binary validation path."
            )
    groups = base.get("groups")
    if not isinstance(groups, list) or len(groups) < 2:
        raise ValueError("Multiclass Monte Carlo base project must define at least two top-level ``groups``.")
    return groups


def generate_run_project_multiclass(
    base_project_path: str | Path,
    run_dir: Path,
    run_id: str,
    output_base: str,
    train_by_label: Dict[str, List[str]],
    val_by_label: Dict[str, List[str]],
    cohort_labels: List[str],
    samples_base_path: str,
) -> Tuple[Path, Path]:
    """
    Write per-cohort ``training_<label>.csv`` (sample names), ``test_<label>.csv`` (absolute paths),
    ``test_groups.json`` for ``methyl-predictor --test-groups``, and a run ``project.json`` with flat
    ``groups`` sample_paths pointing at training CSVs only.

    ``cohort_labels`` order must match ``base`` template ``groups[i].label``.
    """
    with open(base_project_path, encoding="utf-8") as f:
        base = json.load(f)

    groups_template = _assert_flat_multiclass_template(base)
    if len(groups_template) != len(cohort_labels):
        raise ValueError(
            f"Base project has {len(groups_template)} groups but Monte Carlo config has "
            f"{len(cohort_labels)} cohorts; counts must match."
        )
    for i, lbl in enumerate(cohort_labels):
        gl = groups_template[i].get("label")
        if gl != lbl:
            raise ValueError(
                f"Monte Carlo cohorts[{i}].label {lbl!r} != base project groups[{i}].label {gl!r}."
            )

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    train_csv_by_label: Dict[str, Path] = {}
    testing_csv_by_label: Dict[str, Path] = {}
    for lbl in cohort_labels:
        if lbl not in train_by_label or lbl not in val_by_label:
            raise ValueError(f"Missing train/val paths for cohort label {lbl!r}")
        safe = _safe_cohort_filename_label(lbl)
        p = run_dir / f"training_{safe}.csv"
        write_train_csv(p, train_by_label[lbl], samples_base_path)
        train_csv_by_label[lbl] = p
        test_csv = run_dir / f"test_{safe}.csv"
        write_val_csv(test_csv, val_by_label[lbl])
        testing_csv_by_label[lbl] = test_csv

    val_payload: List[Dict[str, Any]] = []
    for lbl in cohort_labels:
        val_payload.append(
            {
                "label": lbl,
                "paths": [str(Path(p).resolve()) for p in val_by_label[lbl]],
            }
        )
    test_groups_json = run_dir / "test_groups.json"
    test_groups_json.parent.mkdir(parents=True, exist_ok=True)
    with open(test_groups_json, "w", encoding="utf-8") as f:
        json.dump(val_payload, f, indent=2)

    project = dict(base)
    project.pop("step_config", None)
    project["output_base"] = output_base.rstrip("/")
    project["project_name"] = run_id
    project["samples_base_path"] = samples_base_path
    new_groups: List[Dict[str, Any]] = []
    for i, g in enumerate(groups_template):
        if not isinstance(g, dict):
            raise ValueError(f"groups[{i}] must be an object")
        gg = dict(g)
        lbl = cohort_labels[i]
        gg["sample_paths"] = [str(train_csv_by_label[lbl].resolve())]
        new_groups.append(gg)
    project["groups"] = new_groups

    project_path = run_dir / "project.json"
    write_run_project_json(project, project_path)

    return project_path, test_groups_json


def _patch_side_groups_with_label_csvs(
    groups: Any,
    csv_by_label: Dict[str, Path],
) -> List[Dict[str, Any]]:
    """Replace sample_paths with per-run CSVs; supports disease ``stages`` (leaf = parent_child)."""
    if not isinstance(groups, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        if item.get("stages"):
            parent = dict(item)
            new_stages: List[Dict[str, Any]] = []
            for st in item.get("stages") or []:
                if not isinstance(st, dict) or not st.get("label"):
                    continue
                plab = str(item.get("label", ""))
                leaf = f"{plab}_{st['label']}"
                if leaf not in csv_by_label:
                    raise ValueError(
                        f"Monte Carlo CSV missing for disease leaf {leaf!r} "
                        f"(expected cohort label in config)."
                    )
                s2 = dict(st)
                s2["sample_paths"] = [str(csv_by_label[leaf].resolve())]
                new_stages.append(s2)
            parent["stages"] = new_stages
            out.append(parent)
        else:
            g2 = dict(item)
            lab = str(g2.get("label", ""))
            if lab not in csv_by_label:
                raise ValueError(f"Monte Carlo CSV missing for group label {lab!r}")
            g2["sample_paths"] = [str(csv_by_label[lab].resolve())]
            out.append(g2)
    return out


def _patch_side_groups_for_mc(
    groups: Any,
    train_csv_by_label: Dict[str, Path],
) -> List[Dict[str, Any]]:
    """Replace sample_paths with per-run train CSVs; supports disease ``stages`` (leaf = parent_child)."""
    return _patch_side_groups_with_label_csvs(groups, train_csv_by_label)


def _n_leaves_in_side_groups(groups: Any) -> int:
    """Count centroid leaves under control/disease ``groups`` (stages = one leaf each)."""
    if not isinstance(groups, list):
        return 0
    n = 0
    for item in groups:
        if not isinstance(item, dict):
            continue
        if item.get("stages"):
            for st in item.get("stages") or []:
                if isinstance(st, dict) and st.get("label"):
                    n += 1
        else:
            n += 1
    return n


def _control_leaf_count_from_project_dict(project: Dict[str, Any]) -> int:
    for key in ("controls", "control"):
        side = project.get(key)
        if isinstance(side, dict) and side.get("groups"):
            return _n_leaves_in_side_groups(side["groups"])
    return 0


def _patch_predictor_side_holdouts_ordered(
    side: Dict[str, Any],
    label_queue: List[str],
    testing_csv_by_label: Dict[str, Path],
) -> Dict[str, Any]:
    """
    Assign ``testing_*.csv`` paths by matching **project resolved leaf order** to predictor layout.

    Predictor JSON may use different parent labels than top-level cohorts (e.g. ``prostate_cancer``
    vs ``pca``); composite keys would not match Monte Carlo cohort labels (``pca_pca1``, …).
    """
    qi = 0
    side = dict(side)
    new_groups: List[Dict[str, Any]] = []
    for item in side.get("groups") or []:
        if not isinstance(item, dict):
            continue
        if item.get("stages"):
            parent = dict(item)
            new_stages: List[Dict[str, Any]] = []
            for st in item.get("stages") or []:
                if not isinstance(st, dict) or not st.get("label"):
                    continue
                if qi >= len(label_queue):
                    raise ValueError(
                        "Monte Carlo: not enough cohort labels for predictor disease stages "
                        f"(need more than {qi} on this side)."
                    )
                lbl = label_queue[qi]
                qi += 1
                if lbl not in testing_csv_by_label:
                    raise ValueError(f"Missing testing CSV for cohort {lbl!r}")
                s2 = dict(st)
                s2["sample_paths"] = [str(testing_csv_by_label[lbl].resolve())]
                new_stages.append(s2)
            parent["stages"] = new_stages
            new_groups.append(parent)
        else:
            g2 = dict(item)
            if qi >= len(label_queue):
                raise ValueError(
                    "Monte Carlo: not enough cohort labels for predictor control groups "
                    f"(need more than {qi} on this side)."
                )
            lbl = label_queue[qi]
            qi += 1
            if lbl not in testing_csv_by_label:
                raise ValueError(f"Missing testing CSV for cohort {lbl!r}")
            g2["sample_paths"] = [str(testing_csv_by_label[lbl].resolve())]
            new_groups.append(g2)
    if qi != len(label_queue):
        raise ValueError(
            f"Monte Carlo: predictor side leaf count ({qi}) != labels provided ({len(label_queue)}): "
            f"{label_queue!r}"
        )
    side["groups"] = new_groups
    return side


def _ensure_predictor_sides_from_top_level_project(
    project: Dict[str, Any], pred: Dict[str, Any]
) -> None:
    """
    When ``step_config.predictor`` omits nested ``controls``/``diseases`` (or empty groups),
    copy cohort shape from top-level project keys so Monte Carlo can still patch holdout CSVs.

    Clears stale ``train_group_paths`` / ``holdout_group_paths`` from templates; MC run projects
    use training CSVs on top-level cohorts and testing CSVs under predictor sides.
    """
    pred.pop("train_group_paths", None)
    pred.pop("holdout_group_paths", None)

    def _copy_side(src_key_plural: str, src_key_singular: str) -> Optional[Dict[str, Any]]:
        for k in (src_key_plural, src_key_singular):
            side = project.get(k)
            if isinstance(side, dict) and side.get("groups"):
                return copy.deepcopy(side)
        return None

    if not isinstance(pred.get("controls"), dict) or not (pred.get("controls") or {}).get("groups"):
        c = _copy_side("controls", "control")
        if c is not None:
            pred["controls"] = c
            pred["control"] = copy.deepcopy(c)
    if not isinstance(pred.get("diseases"), dict) or not (pred.get("diseases") or {}).get("groups"):
        d = _copy_side("diseases", "disease")
        if d is not None:
            pred["diseases"] = d
            pred["disease"] = copy.deepcopy(d)


def _patch_step_config_predictor_multiclass_holdouts(
    project: Dict[str, Any],
    testing_csv_by_label: Dict[str, Path],
    cohort_labels: List[str],
    *,
    embed_test_group_paths: bool,
) -> None:
    """
    Point ``step_config.predictor`` at this run's holdout list files (``testing_*.csv``).

    Top-level ``controls``/``diseases`` already reference training CSVs; the predictor step must not
    keep the base template paths or ``methyl-predictor --project`` would expand the wrong lists.

    **Hierarchical** (``embed_test_group_paths=False``): only nested ``controls`` / ``diseases``,
    preserving template shapes (including parent labels). MethylPredictor resolves multiclass test
    lists by walking those sides in leaf order and zipping with ``get_resolved_groups`` labels.

    **Flat** ``groups`` projects (``embed_test_group_paths=True``): also set ``test_group_paths``
    so multiclass resolution works when predictor sides are absent.
    """
    sc = project.get("step_config")
    if not isinstance(sc, dict):
        return
    project["step_config"] = copy.deepcopy(sc)
    raw_pred = project["step_config"].get("predictor")
    if isinstance(raw_pred, dict):
        pred = copy.deepcopy(raw_pred)
    else:
        pred = {}
    project["step_config"]["predictor"] = pred

    _ensure_predictor_sides_from_top_level_project(project, pred)

    if embed_test_group_paths:
        test_group_paths: List[Dict[str, Any]] = []
        for lbl in cohort_labels:
            if lbl not in testing_csv_by_label:
                raise ValueError(f"Missing testing CSV for cohort {lbl!r}")
            test_group_paths.append(
                {"label": lbl, "paths": [str(testing_csv_by_label[lbl].resolve())]}
            )
        pred["test_group_paths"] = test_group_paths
        # Keep predictor nested controls/diseases aligned to testing CSVs too,
        # but use resolved-order patching so parent labels can differ.
        n_ctrl = _control_leaf_count_from_project_dict(project)
        ctrl_labs = cohort_labels[:n_ctrl]
        dis_labs = cohort_labels[n_ctrl:]
        if isinstance(pred.get("controls"), dict) and pred["controls"].get("groups"):
            pred["controls"] = _patch_predictor_side_holdouts_ordered(
                pred["controls"], ctrl_labs, testing_csv_by_label
            )
        if "control" in pred:
            pred["control"] = (
                copy.deepcopy(pred["controls"])
                if isinstance(pred.get("controls"), dict)
                else pred.get("control")
            )
        if isinstance(pred.get("diseases"), dict) and pred["diseases"].get("groups"):
            pred["diseases"] = _patch_predictor_side_holdouts_ordered(
                pred["diseases"], dis_labs, testing_csv_by_label
            )
        if "disease" in pred:
            pred["disease"] = (
                copy.deepcopy(pred["diseases"])
                if isinstance(pred.get("diseases"), dict)
                else pred.get("disease")
            )
        return
    else:
        pred.pop("test_group_paths", None)
        n_ctrl = _control_leaf_count_from_project_dict(project)
        ctrl_labs = cohort_labels[:n_ctrl]
        dis_labs = cohort_labels[n_ctrl:]
        if isinstance(pred.get("controls"), dict) and pred["controls"].get("groups"):
            pred["controls"] = _patch_predictor_side_holdouts_ordered(
                pred["controls"], ctrl_labs, testing_csv_by_label
            )
        if "control" in pred:
            pred["control"] = (
                copy.deepcopy(pred["controls"])
                if isinstance(pred.get("controls"), dict)
                else pred.get("control")
            )
        if isinstance(pred.get("diseases"), dict) and pred["diseases"].get("groups"):
            pred["diseases"] = _patch_predictor_side_holdouts_ordered(
                pred["diseases"], dis_labs, testing_csv_by_label
            )
        if "disease" in pred:
            pred["disease"] = (
                copy.deepcopy(pred["diseases"])
                if isinstance(pred.get("diseases"), dict)
                else pred.get("disease")
            )
        return

    for key in ("controls", "control"):
        side = pred.get(key)
        if isinstance(side, dict) and side.get("groups"):
            side = dict(side)
            side["groups"] = _patch_side_groups_with_label_csvs(side["groups"], testing_csv_by_label)
            pred[key] = side
    for key in ("diseases", "disease"):
        side = pred.get(key)
        if isinstance(side, dict) and side.get("groups"):
            side = dict(side)
            side["groups"] = _patch_side_groups_with_label_csvs(side["groups"], testing_csv_by_label)
            pred[key] = side


def _write_binary_test_groups_json(
    run_dir: Path,
    val_control_paths: List[str],
    val_disease_paths: List[str],
    control_label: str,
    disease_label: str,
) -> Path:
    """Write per-run holdout manifest for eval split resolvers and gene FeatureCuts."""
    payload = [
        {
            "label": control_label,
            "paths": [str(Path(p).resolve()) for p in val_control_paths],
        },
        {
            "label": disease_label,
            "paths": [str(Path(p).resolve()) for p in val_disease_paths],
        },
    ]
    path = run_dir / "test_groups.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def _patch_step_config_predictor_binary_holdouts(
    project: Dict[str, Any],
    val_control_csv: Path,
    val_disease_csv: Path,
    control_label: str,
    disease_label: str,
) -> None:
    """Set ``step_config.predictor`` control/disease sides to this run's validation CSVs."""
    sc = project.get("step_config")
    if not isinstance(sc, dict):
        project["step_config"] = {}
        sc = project["step_config"]
    project["step_config"] = copy.deepcopy(sc)
    pred = project["step_config"].get("predictor")
    if not isinstance(pred, dict):
        pred = {}
    else:
        pred = copy.deepcopy(pred)
    project["step_config"]["predictor"] = pred
    v_c = str(val_control_csv.resolve())
    v_d = str(val_disease_csv.resolve())
    ctrl = {"label": control_label, "groups": [{"label": control_label, "sample_paths": [v_c]}]}
    dis = {"label": disease_label, "groups": [{"label": disease_label, "sample_paths": [v_d]}]}
    pred["controls"] = ctrl
    pred["diseases"] = dis
    if "control" in pred:
        pred["control"] = copy.deepcopy(ctrl)
    if "disease" in pred:
        pred["disease"] = copy.deepcopy(dis)


def generate_run_project_hierarchical_multiclass(
    base_project_path: str | Path,
    run_dir: Path,
    run_id: str,
    output_base: str,
    train_by_label: Dict[str, List[str]],
    val_by_label: Dict[str, List[str]],
    cohort_labels: List[str],
    samples_base_path: str,
    previous_train_by_label: Optional[Dict[str, List[str]]] = None,
) -> Tuple[Path, Path, Dict[str, Path]]:
    """
    Same artifacts as ``generate_run_project_multiclass`` (``training_<label>.csv``, ``test_<label>.csv``,
    ``test_groups.json``) but keeps ``controls`` / ``diseases`` (and optional nested ``stages``) in
    ``project.json`` for full centroid/detector layout.
    """
    from methyl_utils import load_project

    proj = load_project(base_project_path)
    resolved_order = [x[0] for x in proj.get_resolved_groups()]
    if list(cohort_labels) != list(resolved_order):
        raise ValueError(
            "Monte Carlo cohorts order and labels must match project resolved groups "
            f"(expected {resolved_order!r}, got {list(cohort_labels)!r})."
        )

    with open(base_project_path, encoding="utf-8") as f:
        base = json.load(f)

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    train_csv_by_label: Dict[str, Path] = {}
    testing_csv_by_label: Dict[str, Path] = {}
    for lbl in cohort_labels:
        if lbl not in train_by_label or lbl not in val_by_label:
            raise ValueError(f"Missing train/val paths for cohort label {lbl!r}")
        safe = _safe_cohort_filename_label(lbl)
        p = run_dir / f"training_{safe}.csv"
        write_train_csv(p, train_by_label[lbl], samples_base_path)
        train_csv_by_label[lbl] = p
        test_csv = run_dir / f"test_{safe}.csv"
        write_val_csv(test_csv, val_by_label[lbl])
        testing_csv_by_label[lbl] = test_csv

    val_payload: List[Dict[str, Any]] = []
    for lbl in cohort_labels:
        val_payload.append(
            {
                "label": lbl,
                "paths": [str(Path(p).resolve()) for p in val_by_label[lbl]],
            }
        )
    test_groups_json = run_dir / "test_groups.json"
    test_groups_json.parent.mkdir(parents=True, exist_ok=True)
    with open(test_groups_json, "w", encoding="utf-8") as f:
        json.dump(val_payload, f, indent=2)

    project = dict(base)
    project.pop("step_config", None)
    project["output_base"] = output_base.rstrip("/")
    project["project_name"] = run_id
    project["samples_base_path"] = samples_base_path

    for key in ("controls", "control"):
        side = project.get(key)
        if isinstance(side, dict) and side.get("groups"):
            side = dict(side)
            side["groups"] = _patch_side_groups_for_mc(side["groups"], train_csv_by_label)
            project[key] = side
    for key in ("diseases", "disease"):
        side = project.get(key)
        if isinstance(side, dict) and side.get("groups"):
            side = dict(side)
            side["groups"] = _patch_side_groups_for_mc(side["groups"], train_csv_by_label)
            project[key] = side

    project_path = run_dir / "project.json"
    write_run_project_json(project, project_path)

    centroid_overrides: Dict[str, Path] = {}
    prev_map = previous_train_by_label or {}
    for lbl in cohort_labels:
        safe = _safe_cohort_filename_label(lbl)
        override_path = write_centroid_step_override(
            run_dir / f"centroid_override_{safe}.json",
            prev_map.get(lbl),
            train_by_label[lbl],
        )
        centroid_overrides[lbl] = override_path

    return project_path, test_groups_json, centroid_overrides
