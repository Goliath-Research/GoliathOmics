"""
Resolve MethylAlignmentQC config from a pipeline project config (Pydantic).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from .core.fragmentomics import resolve_fragmentomics_config
from .models.config import AlignmentQCConfig


def resolve_alignment_qc_config(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
) -> AlignmentQCConfig:
    """
    Build AlignmentQCConfig from a project config and optional step overrides.

    By default all resolved groups are included. `step_config.alignment_qc.groups`
    may restrict the set using any mix of:
    - `all`
    - `control`
    - `disease`
    - `group1`, `group2`
    - explicit resolved group labels
    """
    project = load_project(project_path)
    paths = project.get_derived_paths()
    resolved = project.get_resolved_groups()
    resolved_with_side = project._get_resolved_groups_with_side()
    paths_by_label = {label: list(group_paths) for label, group_paths in resolved}

    # Default: all samples from all resolved groups
    sample_paths: List[str] = []
    step_cfg = resolve_for_project("alignment_qc", project)
    groups = step_cfg.get("groups") if step_cfg else None
    if groups is not None:
        seen = set()
        for g in groups:
            selected: List[str] = []
            if g == "all":
                for _, group_paths in resolved:
                    selected.extend(group_paths)
            elif g == "group1":
                selected.extend(project.get_group1_sample_paths())
            elif g == "group2":
                selected.extend(project.get_group2_sample_paths())
            elif g in {"control", "disease"}:
                for _, group_paths, side in resolved_with_side:
                    if side == g:
                        selected.extend(group_paths)
            else:
                selected.extend(paths_by_label.get(g, []))
            for sample_path in selected:
                if sample_path not in seen:
                    sample_paths.append(sample_path)
                    seen.add(sample_path)
    else:
        for _, group_paths in resolved:
            sample_paths.extend(group_paths)

    base: Dict[str, Any] = {
        "sample_paths": sample_paths,
        "output_dir": paths.alignment_qc_dir,
        "validate_schema": True,
    }
    if step_cfg:
        for key in (
            "sample_paths",
            "output_dir",
            "validate_schema",
            "auto_profile_from_analyte",
            "bisulfite_conversion",
            "optional_guardrails",
            "alignment_guardrails",
            "core_guardrails",
            "cycle_screening",
        ):
            if key in step_cfg:
                base[key] = step_cfg[key]

    reg_cfg: Dict[str, Any] = {}
    val_cfg = resolve_for_project("validation", project)
    if isinstance(val_cfg, dict):
        reg = val_cfg.get("regulatory") or {}
        if isinstance(reg, dict):
            reg_cfg = reg

    frag_cfg = resolve_fragmentomics_config(step_cfg, project_regulatory=reg_cfg)
    if frag_cfg is not None:
        base["fragmentomics"] = frag_cfg.model_dump(mode="python")
    elif step_cfg and step_cfg.get("fragmentomics") is not None:
        base["fragmentomics"] = step_cfg["fragmentomics"]

    if step_override_path is not None:
        path = Path(step_override_path)
        if path.exists():
            with open(path) as f:
                overrides = json.load(f)
            for k, v in overrides.items():
                base[k] = v

    return AlignmentQCConfig.model_validate(base)
