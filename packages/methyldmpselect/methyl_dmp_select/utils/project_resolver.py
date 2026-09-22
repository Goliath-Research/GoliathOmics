"""Resolve DmpSelectionConfig from pipeline project JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from ..models.config import DmpSelectionConfig


def _comparison_dirs(
    project: Any,
    comparison: Optional[str],
) -> Tuple[str, str, str, str]:
    """Return (control_label, disease_label, centroid1_dir, centroid2_dir, output_dir)."""
    if project.uses_control_disease():
        comparisons = project.get_comparisons()
        if comparison:
            spec = next(
                (c for c in comparisons if c.comparison_label == comparison or c.disease_group == comparison),
                None,
            )
            if spec is None:
                raise ValueError(f"Unknown comparison {comparison!r}")
        else:
            if len(comparisons) != 1:
                raise ValueError("comparison required when project has multiple comparisons")
            spec = comparisons[0]
        ctrl = spec.control_group
        dis = spec.disease_group
        return (
            ctrl,
            dis,
            project.get_centroid_dir("control", ctrl),
            project.get_centroid_dir("disease", dis),
            project.get_detection_output_dir(ctrl, dis),
        )
    groups = project.get_resolved_groups()
    if len(groups) < 2:
        raise ValueError("Project needs at least two groups for DMP selection")
    ctrl_label = str(groups[0][0])
    dis_label = str(groups[1][0]) if comparison is None else str(comparison)
    return (
        ctrl_label,
        dis_label,
        project.get_centroid_dir("control", ctrl_label),
        project.get_centroid_dir("disease", dis_label),
        project.get_detection_output_dir(ctrl_label, dis_label),
    )


def resolve_dmp_selection_config(
    project_path: Union[str, Path],
    *,
    comparison: Optional[str] = None,
    chromosome: Optional[str] = None,
    step_override_path: Optional[Union[str, Path]] = None,
    output_base_override: Optional[Union[str, Path]] = None,
    resolved_config_path: Optional[Union[str, Path]] = None,
) -> DmpSelectionConfig:
    project = load_project(
        project_path,
        output_base_override=str(output_base_override) if output_base_override else None,
    )
    from methyl_utils.cli_resolved_config import resolve_cli_step_config

    step_cfg: Dict[str, Any] = dict(
        resolve_cli_step_config(
            "dmp_selection",
            project,
            resolved_config_path=resolved_config_path,
            step_override_path=step_override_path,
        )
    )
    if resolved_config_path in (None, ""):
        det_cfg: Dict[str, Any] = dict(resolve_for_project("detection", project))
        for legacy_key in (
            "classifier_dmp_selection",
            "target_balanced_accuracy",
            "featurecuts_max_k_cap",
            "featurecuts_exhaustive_search",
            "featurecuts_max_candidates",
            "min_core_dmps",
            "min_selected_dmps",
            "classifier_export_margin_pct",
            "classifier_export_margin_abs",
            "classifier_export_max_dmps",
            "dynamic_dmp_cutoff_enabled",
            "dynamic_dmp_cutoff_relaxation",
            "validation_split_ratio",
            "validation_n_repeats",
            "temperature",
            "centroid1_validation_samples",
            "centroid2_validation_samples",
            "validation_samples_base_path",
            "random_state",
            "min_dmps_for_export",
            "dmp_export_mode",
        ):
            if legacy_key not in step_cfg and legacy_key in det_cfg:
                step_cfg[legacy_key] = det_cfg[legacy_key]

    _ctrl, _dis, c1_dir, c2_dir, out_dir = _comparison_dirs(project, comparison)
    chromosomes = project.chromosomes or ["1"]
    chrom = str(chromosome or chromosomes[0])
    contexts = list(project.contexts or ["CG"])

    base: Dict[str, Any] = {
        "chromosome": chrom,
        "contexts": contexts,
        "centroid1_dir": c1_dir,
        "centroid2_dir": c2_dir,
        "output_dir": out_dir,
    }
    try:
        base["centroid1_validation_samples"] = project.get_group_sample_paths_by_label(_ctrl)
    except ValueError:
        pass
    try:
        base["centroid2_validation_samples"] = project.get_group_sample_paths_by_label(_dis)
    except ValueError:
        pass
    sb = project.samples_base_path
    if sb:
        base["validation_samples_base_path"] = str(sb).rstrip("/")
    if "selection_mode" not in step_cfg and "classifier_dmp_selection" in step_cfg:
        step_cfg["selection_mode"] = step_cfg.pop("classifier_dmp_selection")
    base.update(step_cfg)
    return DmpSelectionConfig(**base)
