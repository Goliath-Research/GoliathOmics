"""
Resolve MethylDetector config from a pipeline project config (Pydantic).
Uses centroid1_dir and centroid2_dir (first two groups). For N-group multi-class,
run detection one-vs-rest or pairwise and merge DMPs; then use the merged DMP CSV
with the classifier multiclass builder (see build_multiclass_config_from_project).

Merged profile ``actionConfig.detection`` (via ``resolve_for_project``) and overrides are validated with
``MethylDetectorConfig`` (``extra='forbid'``) and explicit errors for removed
legacy keys (see ``reject_legacy_distribution_keys`` in ``config.py``).
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from methyl_utils import ProjectConfig, load_project
from methyl_utils.action_config_resolver import resolve_for_project

from ..models.config import MethylDetectorConfig
from .multiclass_export_config import filter_detection_config_for_detector


def _attach_samples_base(cfg: Dict[str, object], project: ProjectConfig) -> None:
    """Propagate project-level samples_base_path into detector validation config."""
    sb = project.samples_base_path
    if sb:
        cfg["validation_samples_base_path"] = str(sb).rstrip("/")


def resolve_detector_config_per_cancer_group(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    output_base_override: Optional[Union[str, Path]] = None,
    control_index: int = 0,
) -> List[Tuple[MethylDetectorConfig, str]]:
    """
    Build one MethylDetectorConfig per comparison (control vs disease pair).
    When project uses control/disease + comparisons: one config per comparison from get_comparisons().
    When project uses flat groups: one config per non-control group (control index 0 vs each other).
    If output_base_override is set, all paths (centroid dirs, output_dir) are derived from that base
    instead of the project's output_base (e.g. for running on a different machine).
    """
    project = load_project(
        project_path,
        output_base_override=str(output_base_override) if output_base_override is not None else None,
    )
    step_cfg = resolve_for_project("detection", project)
    if step_override_path is not None:
        with open(step_override_path) as f:
            overrides = json.load(f)
        overrides = {
            k: v
            for k, v in overrides.items()
            if v is not None and k not in ("context", "comparison", "base_config")
        }
        step_cfg = {**step_cfg, **overrides}
    step_cfg = filter_detection_config_for_detector(step_cfg)

    if project.uses_control_disease():
        comparisons = project.get_comparisons()
        out: List[Tuple[MethylDetectorConfig, str]] = []
        for spec in comparisons:
            ctrl_label = spec.control_group
            dis_label = spec.disease_group
            comp_label = spec.comparison_label or spec.disease_group
            base: Dict[str, object] = {
                "chromosome": project.chromosomes or ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                    "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "X", "Y"],
                "contexts": project.contexts or ["CG"],
                "centroid1_dir": project.get_centroid_dir("control", ctrl_label),
                "centroid2_dir": project.get_centroid_dir("disease", dis_label),
                "output_dir": project.get_detection_output_dir(ctrl_label, dis_label),
            }
            try:
                base["centroid1_validation_samples"] = project.get_group_sample_paths_by_label(ctrl_label)
            except ValueError:
                pass
            try:
                base["centroid2_validation_samples"] = project.get_group_sample_paths_by_label(dis_label)
            except ValueError:
                pass
            for k, v in step_cfg.items():
                base[k] = v
            # Always use comparison-based structure: detections/<control_group>/<disease_group>
            base["output_dir"] = project.get_detection_output_dir(ctrl_label, dis_label)
            base["centroid1_dir"] = project.get_centroid_dir("control", ctrl_label)
            base["centroid2_dir"] = project.get_centroid_dir("disease", dis_label)
            _attach_samples_base(base, project)
            out.append((MethylDetectorConfig.model_validate(base), comp_label))
        return out

    paths = project.get_derived_paths()
    resolved = project.get_resolved_groups()
    if len(resolved) < 2:
        return []
    centroid_dirs = paths.centroid_dirs or [paths.centroid1_dir, paths.centroid2_dir]
    if len(centroid_dirs) != len(resolved):
        centroid_dirs = []
        for i, (label, _) in enumerate(resolved):
            side = "control" if i == control_index else "disease"
            centroid_dirs.append(project.get_centroid_dir(side, label))
    c1_dir = centroid_dirs[control_index]
    out = []
    for i in range(len(resolved)):
        if i == control_index:
            continue
        label = resolved[i][0]
        base = {
            "chromosome": project.chromosomes or ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "X", "Y"],
            "contexts": project.contexts or ["CG"],
            "centroid1_dir": c1_dir,
            "centroid2_dir": centroid_dirs[i],
            "output_dir": project.get_detection_output_dir(resolved[control_index][0], label),
        }
        if project.get_group_sample_paths(control_index):
            base["centroid1_validation_samples"] = project.get_group_sample_paths(control_index)
        if project.get_group_sample_paths(i):
            base["centroid2_validation_samples"] = project.get_group_sample_paths(i)
        for k, v in step_cfg.items():
            base[k] = v
        _attach_samples_base(base, project)
        out.append((MethylDetectorConfig.model_validate(base), label))
    return out


def resolve_detector_config(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    output_base_override: Optional[Union[str, Path]] = None,
    centroid1_dir_override: Optional[Union[str, Path]] = None,
    centroid2_dir_override: Optional[Union[str, Path]] = None,
    resolved_config_path: Optional[Union[str, Path]] = None,
) -> MethylDetectorConfig:
    """
    Build MethylDetectorConfig from a project config and optional step overrides.
    Uses Pydantic throughout; returns MethylDetectorConfig (not dict).
    Output dir follows detections/<control_group>/<disease_group> (e.g. detections/healthy/cancer).
    If output_base_override is set, centroid and output paths are derived from that base instead
    of the project's output_base (so one project JSON works across machines). Optional
    centroid1_dir_override / centroid2_dir_override still override those specific paths when needed.
    """
    project = load_project(project_path)
    if output_base_override is not None:
        project = project.model_copy(update={"output_base": str(output_base_override)})
    paths = project.get_derived_paths()

    if project.uses_control_disease() and len(project.get_comparisons()) == 1:
        spec = project.get_comparisons()[0]
        output_dir = project.get_detection_output_dir(spec.control_group, spec.disease_group)
    else:
        output_dir = paths.detection_dir

    base: Dict[str, object] = {
        "chromosome": project.chromosomes or ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
            "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "X", "Y"],
        "contexts": project.contexts or ["CG"],
        "centroid1_dir": str(centroid1_dir_override) if centroid1_dir_override is not None else paths.centroid1_dir,
        "centroid2_dir": str(centroid2_dir_override) if centroid2_dir_override is not None else paths.centroid2_dir,
        "output_dir": output_dir,
    }
    # Optional: use project group sample paths as validation samples (detector can use "use_metadata" instead)
    if project.get_group1_sample_paths():
        base["centroid1_validation_samples"] = project.get_group1_sample_paths()
    if project.get_group2_sample_paths():
        base["centroid2_validation_samples"] = project.get_group2_sample_paths()

    _attach_samples_base(base, project)

    # Apply detection step config (worker path uses --resolved-config; standalone uses profile/env).
    from methyl_utils.cli_resolved_config import resolve_cli_step_config

    step_cfg = filter_detection_config_for_detector(
        resolve_cli_step_config(
            "detection",
            project,
            resolved_config_path=resolved_config_path,
            step_override_path=step_override_path,
        )
    )
    if step_cfg:
        for k, v in step_cfg.items():
            base[k] = v
        if project.uses_control_disease() and len(project.get_comparisons()) == 1:
            spec = project.get_comparisons()[0]
            base["output_dir"] = project.get_detection_output_dir(spec.control_group, spec.disease_group)

    # CLI overrides for centroid dirs (apply after step config so they take precedence)
    if centroid1_dir_override is not None:
        base["centroid1_dir"] = str(centroid1_dir_override)
    if centroid2_dir_override is not None:
        base["centroid2_dir"] = str(centroid2_dir_override)

    return MethylDetectorConfig.model_validate(base)
