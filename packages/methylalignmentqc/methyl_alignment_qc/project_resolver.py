"""
Resolve MethylAlignmentQC config from a pipeline project config (Pydantic).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import (
    deep_merge,
    load_resolved_config,
    resolve_for_project,
)

from .core.fragmentomics import resolve_fragmentomics_config
from .models.config import AlignmentQCConfig


def alignment_qc_window(
    *,
    resolved_config: Optional[Mapping[str, Any]] = None,
    sample_context: Optional[Mapping[str, Any]] = None,
) -> Optional[AlignmentQCConfig]:
    """Coerce a baked instance window plus a sample overlay. No project, no site env."""
    merged: Dict[str, Any] = {}
    if isinstance(resolved_config, Mapping):
        merged = load_resolved_config("alignment_qc", resolved_config=dict(resolved_config))
    overlay = sample_alignment_overlay(sample_context)
    if overlay:
        merged = deep_merge(merged, overlay)
    if not merged:
        return None
    payload: Dict[str, Any] = {"sample_paths": ["/"], "output_dir": "/"}
    payload.update(merged)
    if not payload.get("sample_paths"):
        payload["sample_paths"] = ["/"]
    if not payload.get("output_dir"):
        payload["output_dir"] = "/"
    return AlignmentQCConfig.model_validate(payload)


def sample_alignment_overlay(sample_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Sparse alignment_qc overlay carried on one foreach sample record.

    Instance bake (``resolvedConfig``) is the merged site/profile/instance window.
    A sample may pin ``core_guardrails`` or ``actionConfig.alignment_qc`` on top.
    """
    if not isinstance(sample_context, Mapping):
        return {}
    overlay: Dict[str, Any] = {}
    action = sample_context.get("actionConfig")
    if isinstance(action, Mapping):
        alignment = action.get("alignment_qc")
        if isinstance(alignment, Mapping):
            overlay = deep_merge(overlay, dict(alignment))
    guardrails = sample_context.get("core_guardrails")
    if isinstance(guardrails, Mapping):
        overlay = deep_merge(overlay, {"core_guardrails": dict(guardrails)})
    return overlay


def resolve_alignment_qc_config(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    *,
    resolved_config: Optional[Mapping[str, Any]] = None,
    sample_context: Optional[Mapping[str, Any]] = None,
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
    # Sample prep runs alignment QC before methylation extraction writes
    # <chrom>-<ctx>.h5. The analysis H5 filter would drop every sample.
    paths = project.get_derived_paths(require_h5=False)
    resolved = project.get_resolved_groups(require_h5=False)
    resolved_with_side = project._get_resolved_groups_with_side(require_h5=False)
    paths_by_label = {label: list(group_paths) for label, group_paths in resolved}

    # Default: all samples from all resolved groups
    sample_paths: List[str] = []
    # Baked task resolvedConfig is the instance window. Do not re-read site/profile
    # from the worker environment when it is present (workers hold no DB secrets
    # and must not substitute the published default for an instance override).
    if isinstance(resolved_config, Mapping):
        step_cfg = load_resolved_config("alignment_qc", resolved_config=dict(resolved_config))
    else:
        step_cfg = resolve_for_project("alignment_qc", project)
    sample_overlay = sample_alignment_overlay(sample_context)
    if sample_overlay:
        step_cfg = deep_merge(dict(step_cfg or {}), sample_overlay)
    groups = step_cfg.get("groups") if step_cfg else None
    if groups is not None:
        seen = set()
        for g in groups:
            selected: List[str] = []
            if g == "all":
                for _, group_paths in resolved:
                    selected.extend(group_paths)
            elif g == "group1":
                selected.extend(project.get_group1_sample_paths(require_h5=False))
            elif g == "group2":
                selected.extend(project.get_group2_sample_paths(require_h5=False))
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
