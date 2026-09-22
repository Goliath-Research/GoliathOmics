"""
Resolve MethylCluster config from a pipeline project config.

When a group has subcluster enabled, this module builds MethylClusterConfig from
the project (sample paths, chrom, ctx, output_dir) and provides helpers to write
the clustering manifest (derived labels and sample paths per cluster) after clustering.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from .config import MethylClusterConfig, ClusteringMethod, ClusterMetric

logger = logging.getLogger(__name__)

CLUSTER_MANIFEST_FILENAME = "manifest.json"
ASSIGNMENTS_CSV_FILENAME = "assignments.csv"


def resolve_cluster_config_for_group(
    project_path: Union[str, Path],
    group_label: str,
    step_override: Optional[Dict[str, Any]] = None,
) -> Tuple[MethylClusterConfig, Literal["control", "disease"]]:
    """
    Build MethylClusterConfig for one group that has subcluster enabled.

    Args:
        project_path: Path to project JSON config.
        group_label: Group label (e.g. "healthy"). Must be a group with subcluster enabled,
            or a group that exists in the project (for standalone cluster run).
        step_override: Optional dict of config overrides (e.g. from step_config.cluster).

    Returns:
        (MethylClusterConfig, side) where side is "control" or "disease".
    """
    project = load_project(project_path)
    paths = project.get_derived_paths()
    project_root = project.get_project_root()

    # Prefer control/disease layout for output_dir
    with_subcluster = project.get_groups_with_subcluster()
    side: Optional[Literal["control", "disease"]] = None
    group_config = None
    for s, label, g in with_subcluster:
        if label == group_label:
            side = s
            group_config = g
            break

    if side is None or group_config is None:
        # No subcluster for this group; still allow running cluster (e.g. exploratory)
        # Resolve side from resolved groups: first half = control, second = disease
        resolved = project._get_resolved_groups_with_side()
        for i, (lbl, _, s) in enumerate(resolved):
            if lbl == group_label:
                side = s
                break
        if side is None:
            raise ValueError(
                f"Group {group_label!r} not found in project. "
                f"Available labels: {[r[0] for r in project._get_resolved_groups_with_side()]}"
            )
        group_config = None

    sample_paths = project.get_group_sample_paths_by_label(group_label)
    if len(sample_paths) < 2:
        raise ValueError(
            f"Group {group_label!r} has {len(sample_paths)} samples; at least 2 required for clustering"
        )

    output_dir = project.get_clustering_output_dir(side, group_label)

    chrom = project.chromosomes[0] if project.chromosomes else "1"
    ctx = project.contexts[0] if project.contexts else "CG"

    # Build config from SubclusterRequest if present
    method = ClusteringMethod.CENTROID
    metric = ClusterMetric.JENSEN_SHANNON
    min_cluster_size = 5
    force_k: Optional[int] = None
    if group_config and group_config.subcluster and group_config.subcluster.enabled:
        req = group_config.subcluster
        method = ClusteringMethod(req.method)
        try:
            metric = ClusterMetric(req.metric)
        except ValueError:
            metric = ClusterMetric.JENSEN_SHANNON
        min_cluster_size = req.min_cluster_size if req.min_cluster_size is not None else 5
        force_k = req.force_k

    config = MethylClusterConfig(
        samples=sample_paths,
        chrom=chrom,
        ctx=ctx,
        clustering_method=method,
        metric=metric,
        min_cluster_size=min_cluster_size,
        force_k=force_k,
        output_dir=output_dir,
        cache_distance_matrix=True,
        use_gpu=True,
    )

    # Apply project step_config.cluster if present
    step_cfg = resolve_for_project("cluster", project)
    if step_override:
        step_cfg = {**step_cfg, **step_override}
    valid_fields = set(MethylClusterConfig.model_fields.keys())
    for key, value in step_cfg.items():
        if key in valid_fields:
            setattr(config, key, value)

    return config, side


def write_clustering_manifest(
    output_dir: Union[str, Path],
    parent_label: str,
    results: Dict[str, Any],
    side: Literal["control", "disease"],
) -> Path:
    """
    Write manifest.json and assignments.csv after MethylCluster.run().

    manifest.json format:
      {
        "parent_label": "healthy",
        "side": "control",
        "derived_labels": ["healthy_c0", "healthy_c1"],
        "groups": { "healthy_c0": ["/path/1", ...], "healthy_c1": ["/path/2", ...] }
      }

    assignments.csv: sample_path, cluster_id, derived_label
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cluster_assignments = results.get("cluster_assignments", {})
    # Exclude noise if present
    derived_labels: List[str] = []
    groups: Dict[str, List[str]] = {}
    for key in sorted(cluster_assignments.keys()):
        if key == "noise":
            continue
        # key is cluster_0, cluster_1 or from forced_groups
        if key.startswith("cluster_"):
            k = int(key.split("_")[1])
            derived_labels.append(f"{parent_label}_c{k}")
        else:
            # Use key as suffix (e.g. healthy, diseased from forced_groups)
            derived_labels.append(f"{parent_label}_{key}")
        groups[derived_labels[-1]] = list(cluster_assignments[key])

    manifest = {
        "parent_label": parent_label,
        "side": side,
        "derived_labels": derived_labels,
        "groups": groups,
    }
    manifest_path = output_dir / CLUSTER_MANIFEST_FILENAME
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    logger.info("Wrote %s", manifest_path)

    # assignments.csv: sample_path, cluster_id, derived_label
    sample_paths = results.get("sample_paths", [])
    labels = results.get("labels", [])
    if len(sample_paths) != len(labels):
        logger.warning("Mismatch sample_paths vs labels length; skipping assignments.csv")
    else:
        # Build cluster_id -> derived_label
        id_to_label: Dict[int, str] = {}
        for idx, dl in enumerate(derived_labels):
            id_to_label[idx] = dl
        id_to_label[-1] = "noise"
        rows: List[str] = ["sample_path,cluster_id,derived_label"]
        for path, lab in zip(sample_paths, labels):
            dl = id_to_label.get(int(lab), f"cluster_{lab}")
            rows.append(f"{path},{lab},{dl}")
        assignments_path = output_dir / ASSIGNMENTS_CSV_FILENAME
        with open(assignments_path, "w") as f:
            f.write("\n".join(rows))
        logger.info("Wrote %s", assignments_path)

    return manifest_path


def get_groups_with_subcluster(project_path: Union[str, Path]) -> List[Tuple[str, Literal["control", "disease"]]]:
    """Return [(group_label, side), ...] for all groups that have subcluster enabled."""
    project = load_project(project_path)
    return [(label, side) for side, label, _ in project.get_groups_with_subcluster()]
