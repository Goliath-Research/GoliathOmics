"""
Resolve MethylCentroid batch config from a pipeline project config (Pydantic).
Supports two-group (group1/group2) and N-group projects.
When a group has subcluster (and persist_centroids), runs MethylCluster first then
MethylCentroid once per discovered cluster (Option C: centroid step drives clustering).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from methyl_utils import load_project
from methyl_utils.cli_resolved_config import resolve_cli_step_config

from .config import BatchProcessingConfig, MethylCentroidConfig

logger = logging.getLogger(__name__)

CLUSTER_MANIFEST_FILENAME = "manifest.json"


def _forbid_centroid_samples_key(step_dict: Any, source: str) -> None:
    """Reject obsolete ``samples`` in centroid step config or overrides (strict migration)."""
    if not isinstance(step_dict, dict):
        return
    bc = step_dict.get("base_config")
    if isinstance(bc, dict) and "samples" in bc:
        raise ValueError(
            f'{source}: obsolete key "samples" in centroid base_config is no longer supported. '
            "Remove it from project step_config and step-override JSON. "
            "Pipeline runs use add_samples for the cohort; incremental updates use add_samples/remove_samples "
            "with baseline cohort read from existing centroid HDF5 metadata."
        )


def _stamp_residualize_coef_dir(
    batch: BatchProcessingConfig,
    residualize_coef_dir: Optional[Union[str, Path]] = None,
    step_cfg: Optional[Dict[str, Any]] = None,
) -> BatchProcessingConfig:
    """Bind optional residualize coefficients; absent means raw-beta (shipped) path."""
    coef = residualize_coef_dir
    if coef is None and isinstance(step_cfg, dict):
        coef = step_cfg.get("residualize_coef_dir")
        bc = step_cfg.get("base_config")
        if coef is None and isinstance(bc, dict):
            coef = bc.get("residualize_coef_dir")
    if not coef:
        return batch
    return batch.model_copy(
        update={
            "base_config": batch.base_config.model_copy(
                update={"residualize_coef_dir": str(coef)}
            )
        }
    )


def group_token_requests_all_groups(
    project_path: Union[str, Path],
    group: Union[str, int],
) -> bool:
    """
    True when ``--group all`` should run every resolved cohort.

    When the project defines a group label ``all`` (common for pooled controls),
    ``--group all`` targets that cohort instead of running all groups.
    """
    if isinstance(group, int):
        return False
    token = str(group).strip().lower()
    if token != "all":
        return False
    project = load_project(project_path)
    for label, _paths in project.get_resolved_groups():
        if str(label).casefold() == "all":
            return False
    return True


def _normalize_centroid_group_arg(
    project: Any,
    group: Union[str, int],
) -> Union[str, int]:
    """
    Map CLI ``--group`` tokens to group1/group2 aliases or a 0-based index.

    Accepts project group labels (e.g. ``all``, ``PCa``) in addition to
    ``group1``, ``group2``, and integer indices.
    """
    if isinstance(group, int):
        return group
    token = str(group).strip()
    lower = token.lower()
    if lower == "group1":
        return "group1"
    if lower == "group2":
        return "group2"
    resolved = project.get_resolved_groups()
    for i, (label, _paths) in enumerate(resolved):
        if str(label).casefold() == lower:
            return i
    valid = ", ".join(repr(label) for label, _ in resolved)
    raise ValueError(
        f"group {token!r} not found; use group1, group2, a 0-based index, "
        f"or a project group label ({valid})"
    )


def _get_group_config_for_label(project: Any, side: str, label: str) -> Optional[Any]:
    """Return the GroupConfig for (side, label) if project uses control/disease; else None."""
    if not project.uses_control_disease():
        return None
    if side == "control" and project.control:
        for g in project.control.groups:
            if g.label == label:
                return g
            if g.stages:
                for st in g.stages:
                    if f"{g.label}_{st.label}" == label:
                        return st
    if side == "disease" and project.disease:
        for g in project.disease.groups:
            if g.label == label:
                return g
            if g.stages:
                for st in g.stages:
                    if f"{g.label}_{st.label}" == label:
                        return st
    return None


def _resolve_subcluster_centroid_output_dir(
    project: Any,
    side: str,
    group_label: str,
    derived_label: str,
    output_dir: Optional[Union[str, Path]],
    *,
    n_derived_labels: int,
) -> str:
    """
    Resolve centroid output dir for one derived sub-cluster.

    Without an override, each derived label uses ``get_centroid_dir(side, derived_label)``.
    With an override, a single derived label uses the override path as-is; multiple derived
    labels map sibling paths under the override base (mirroring project layout) so
    ``{chrom}-{ctx}.h5`` files do not collide.
    """
    canonical_derived = Path(project.get_centroid_dir(side, derived_label)).expanduser().resolve()
    if output_dir is None:
        return str(canonical_derived)
    base = Path(output_dir).expanduser().resolve()
    if n_derived_labels <= 1:
        return str(base)
    canonical_parent = Path(project.get_centroid_dir(side, group_label)).expanduser().resolve()
    parent_parent = canonical_parent.parent
    try:
        rel = canonical_derived.relative_to(parent_parent)
    except ValueError:
        rel = Path(derived_label)
    return str(base.parent / rel)


def _run_cluster_then_centroids_per_cluster(
    project_path: Union[str, Path],
    side: str,
    group_label: str,
    step_override_path: Optional[Union[str, Path]] = None,
    *,
    output_dir: Optional[Union[str, Path]] = None,
    chromosome: Optional[str] = None,
    context: Optional[str] = None,
    resolved_config_path: Optional[Union[str, Path]] = None,
) -> None:
    """
    Run MethylCluster for the group, then MethylCentroid once per derived cluster.
    Writes centroids to get_centroid_dir(side, derived_label) for each derived_label.
    """
    from .cli import run_batch_processing

    project = load_project(project_path)
    clustering_dir = Path(project.get_clustering_output_dir(side, group_label))
    manifest_path = clustering_dir / CLUSTER_MANIFEST_FILENAME

    if not manifest_path.exists():
        logger.info("Running MethylCluster for group %s (manifest not found)", group_label)
        try:
            from methyl_cluster.project_resolver import (
                resolve_cluster_config_for_group,
                write_clustering_manifest,
            )
            from methyl_cluster.cluster import MethylCluster
        except ImportError as err:
            raise RuntimeError(
                "Subcluster group requires methyl_cluster. Install it or run clustering first: "
                "python -m methyl_cluster.cli --project <project> --group " + group_label
            ) from err
        config, _ = resolve_cluster_config_for_group(project_path, group_label, step_override)
        cluster = MethylCluster(config)
        results = cluster.run()
        write_clustering_manifest(config.output_dir, group_label, results, side)

    with open(manifest_path) as f:
        manifest = json.load(f)
    derived_labels: List[str] = manifest.get("derived_labels", [])
    groups: Dict[str, List[str]] = manifest.get("groups", {})
    if not derived_labels or not groups:
        logger.warning("Empty manifest at %s; skipping centroid build for %s", manifest_path, group_label)
        return

    chromosomes = project.chromosomes or [
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
        "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "X", "Y",
    ]
    contexts = project.contexts or ["CG"]
    if chromosome is not None:
        chromosomes = [str(chromosome)]
    if context is not None:
        contexts = [str(context)]
    chrom = chromosomes[0]
    ctx = contexts[0]
    step_cfg = resolve_cli_step_config(
        "centroid",
        project,
        resolved_config_path=resolved_config_path,
        step_override_path=step_override_path,
    )
    _forbid_centroid_samples_key(step_cfg, "centroid step config (subcluster)")

    n_derived = len(derived_labels)
    for derived_label in derived_labels:
        sample_paths = groups.get(derived_label)
        if not sample_paths:
            continue
        cluster_output_dir = _resolve_subcluster_centroid_output_dir(
            project,
            side,
            group_label,
            derived_label,
            output_dir,
            n_derived_labels=n_derived,
        )
        base_config = MethylCentroidConfig(
            laboratory=project.project_name,
            disease="",
            group=derived_label,
            batch=project.project_name,
            chrom=chrom,
            ctx=ctx,
            output_dir=cluster_output_dir,
            add_samples=sample_paths,
            min_coverage=step_cfg.get("base_config", {}).get("min_coverage", 4),
            use_gpu=step_cfg.get("base_config", {}).get("use_gpu", True),
        )
        batch = BatchProcessingConfig(
            chromosomes=chromosomes,
            contexts=contexts,
            base_config=base_config,
        )
        if "base_config" in step_cfg:
            for k, v in step_cfg["base_config"].items():
                if k != "output_dir" and hasattr(batch.base_config, k):
                    setattr(batch.base_config, k, v)
        run_batch_processing(batch)


def run_centroids_for_all_groups(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    resolved_config_path: Optional[Union[str, Path]] = None,
    residualize_coef_dir: Optional[Union[str, Path]] = None,
) -> None:
    """
    Run MethylCentroid batch for every resolved group. For groups with subcluster and
    persist_centroids, runs MethylCluster first then one centroid per derived cluster.
    """
    from .cli import run_batch_processing

    project = load_project(project_path)
    with_side: List[Tuple[str, List[str], str]] = project._get_resolved_groups_with_side()

    for i, (label, paths, side) in enumerate(with_side):
        group_config = _get_group_config_for_label(project, side, label)
        if (
            group_config
            and group_config.subcluster is not None
            and group_config.subcluster.enabled
            and group_config.subcluster.persist_centroids
        ):
            _run_cluster_then_centroids_per_cluster(
                project_path, side, label, step_override_path,
                resolved_config_path=resolved_config_path,
            )
        else:
            batch = resolve_centroid_batch_config(
                project_path, i, step_override_path,
                resolved_config_path=resolved_config_path,
                residualize_coef_dir=residualize_coef_dir,
            )
            run_batch_processing(batch)


def run_centroid_for_one_group(
    project_path: Union[str, Path],
    group: Union[str, int],
    step_override_path: Optional[Union[str, Path]] = None,
    *,
    output_dir: Optional[Union[str, Path]] = None,
    chromosome: Optional[str] = None,
    context: Optional[str] = None,
    resolved_config_path: Optional[Union[str, Path]] = None,
    residualize_coef_dir: Optional[Union[str, Path]] = None,
) -> None:
    """
    Run MethylCentroid for a single group (by index or 'group1'/'group2').
    If the group has subcluster + persist_centroids, runs MethylCluster then centroid per cluster.
    """
    from .cli import run_batch_processing

    project = load_project(project_path)
    group = _normalize_centroid_group_arg(project, group)
    if not isinstance(group, int) or not project.uses_control_disease():
        batch = resolve_centroid_batch_config(
            project_path,
            group,
            step_override_path,
            output_dir_override=output_dir,
            chromosome=chromosome,
            context=context,
            resolved_config_path=resolved_config_path,
            residualize_coef_dir=residualize_coef_dir,
        )
        run_batch_processing(batch)
        return
    with_side = project._get_resolved_groups_with_side()
    if group < 0 or group >= len(with_side):
        batch = resolve_centroid_batch_config(
            project_path,
            group,
            step_override_path,
            output_dir_override=output_dir,
            chromosome=chromosome,
            context=context,
            resolved_config_path=resolved_config_path,
            residualize_coef_dir=residualize_coef_dir,
        )
        run_batch_processing(batch)
        return
    label, _paths, side = with_side[group]
    group_config = _get_group_config_for_label(project, side, label)
    if (
        group_config
        and group_config.subcluster is not None
        and group_config.subcluster.enabled
        and group_config.subcluster.persist_centroids
    ):
        _run_cluster_then_centroids_per_cluster(
            project_path,
            side,
            label,
            step_override_path,
            output_dir=output_dir,
            chromosome=chromosome,
            context=context,
            resolved_config_path=resolved_config_path,
        )
    else:
        batch = resolve_centroid_batch_config(
            project_path,
            group,
            step_override_path,
            output_dir_override=output_dir,
            chromosome=chromosome,
            context=context,
            resolved_config_path=resolved_config_path,
            residualize_coef_dir=residualize_coef_dir,
        )
        run_batch_processing(batch)


def resolve_centroid_batch_config(
    project_path: Union[str, Path],
    group: Union[str, int],
    step_override_path: Optional[Union[str, Path]] = None,
    *,
    output_dir_override: Optional[Union[str, Path]] = None,
    chromosome: Optional[str] = None,
    context: Optional[str] = None,
    resolved_config_path: Optional[Union[str, Path]] = None,
    residualize_coef_dir: Optional[Union[str, Path]] = None,
) -> BatchProcessingConfig:
    """
    Build BatchProcessingConfig for one group from a project config.
    group may be "group1", "group2", an integer index 0, 1, ..., or a project group label.
    Returns Pydantic BatchProcessingConfig.
    """
    project = load_project(project_path)
    group = _normalize_centroid_group_arg(project, group)
    paths = project.get_derived_paths()
    resolved = project.get_resolved_groups()

    if isinstance(group, int):
        if group < 0 or group >= len(resolved):
            raise ValueError(f"group index {group} out of range (have {len(resolved)} groups)")
        label = resolved[group][0]
        sample_paths = list(resolved[group][1])
        if not sample_paths:
            raise ValueError(
                f"No samples resolved for group index {group} (label {label!r}). "
                "If this label is a disease type parent with nested 'stages', the installed "
                "methyl_utils may be too old to expand stages (parent groups have no sample_paths). "
                "From the repo root: pip install -e packages/methylutils"
            )
        centroid_dirs = paths.centroid_dirs or [paths.centroid1_dir, paths.centroid2_dir]
        if group < len(centroid_dirs):
            output_dir = centroid_dirs[group]
        else:
            side = "control" if group == 0 else "disease"
            output_dir = project.get_centroid_dir(side, label)
    elif group == "group1":
        label = resolved[0][0]
        sample_paths = list(resolved[0][1])
        output_dir = paths.centroid1_dir
        if not sample_paths:
            raise ValueError(
                f"No samples resolved for group1 (label {label!r}). "
                "For disease groups with nested 'stages', upgrade methyl_utils "
                "(pip install -e packages/methylutils)."
            )
    elif group == "group2":
        if len(resolved) < 2:
            raise ValueError("project has only one group; use group1 or index 0")
        label = resolved[1][0]
        sample_paths = list(resolved[1][1])
        output_dir = paths.centroid2_dir
        if not sample_paths:
            raise ValueError(
                f"No samples resolved for group2 (label {label!r}). "
                "For disease groups with nested 'stages', upgrade methyl_utils "
                "(pip install -e packages/methylutils)."
            )
    else:
        valid = ", ".join(repr(label) for label, _ in resolved)
        raise ValueError(
            "group must be 'group1', 'group2', an integer index, "
            f"or a project group label ({valid})"
        )

    base_config = MethylCentroidConfig(
        laboratory=project.project_name,
        disease="",
        group=label,
        batch=project.project_name,
        chrom=project.chromosomes[0] if project.chromosomes else "1",
        ctx=project.contexts[0] if project.contexts else "CG",
        output_dir=output_dir,
        add_samples=sample_paths,
        min_coverage=4,
        use_gpu=True,
    )
    batch = BatchProcessingConfig(
        chromosomes=project.chromosomes or ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
            "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "X", "Y"],
        contexts=project.contexts or ["CG"],
        base_config=base_config,
    )
    # Apply project-level step config (centroid) if present (never override output_dir)
    step_cfg = resolve_cli_step_config(
        "centroid",
        project,
        resolved_config_path=resolved_config_path,
        step_override_path=step_override_path,
    )
    if step_cfg:
        _forbid_centroid_samples_key(step_cfg, "project step_config.centroid")
        if "base_config" in step_cfg:
            base_data = batch.base_config.model_dump()
            for k, v in step_cfg["base_config"].items():
                if k != "output_dir":  # keep canonical path from get_derived_paths()
                    base_data[k] = v
            batch = batch.model_copy(update={"base_config": MethylCentroidConfig(**base_data)})
        for key in ("chromosomes", "contexts", "continue_on_error", "save_batch_summary"):
            if key in step_cfg:
                batch = batch.model_copy(update={key: step_cfg[key]})
    # Keep canonical project paths unless the worker/CLI passed an explicit output dir.
    if output_dir_override is not None:
        canonical_output = str(Path(output_dir_override).expanduser().resolve())
    elif isinstance(group, int):
        canonical_output = (
            paths.centroid_dirs[group]
            if (paths.centroid_dirs and group < len(paths.centroid_dirs))
            else output_dir
        )
    else:
        canonical_output = paths.centroid1_dir if group == "group1" else paths.centroid2_dir
    batch = batch.model_copy(
        update={"base_config": batch.base_config.model_copy(update={"output_dir": canonical_output})}
    )
    if chromosome is not None:
        batch = batch.model_copy(update={"chromosomes": [str(chromosome)]})
    if context is not None:
        batch = batch.model_copy(update={"contexts": [str(context)]})
    return _stamp_residualize_coef_dir(batch, residualize_coef_dir, step_cfg)
