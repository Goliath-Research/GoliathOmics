"""Resolve cell-deconvolution config and sample paths from a pipeline project."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from .config import CellDeconvStepConfig


def _all_sample_dirs(project) -> List[Tuple[str, str, str]]:  # noqa: ANN001
    """Return (sample_id, sample_dir, resolved_group_label) for project samples."""
    seen: set[str] = set()
    out: List[Tuple[str, str, str]] = []
    for label, group_paths in project.get_resolved_groups():
        for p in group_paths:
            if p in seen:
                continue
            seen.add(p)
            sample_id = Path(p).name
            out.append((sample_id, p, str(label)))
    return out


def resolve_cell_deconv_step_config(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    resolved_config: Optional[Dict[str, Any]] = None,
) -> Tuple[CellDeconvStepConfig, List[Tuple[str, str, str]], str]:
    project = load_project(project_path)
    paths = project.get_derived_paths()
    step_cfg: Dict[str, Any] = dict(resolve_for_project("cell_deconvolution", project))
    if isinstance(resolved_config, dict):
        nested = resolved_config.get("cell_deconvolution")
        if isinstance(nested, dict):
            step_cfg.update(nested)
        else:
            for key in CellDeconvStepConfig.model_fields:
                if resolved_config.get(key) is not None:
                    step_cfg[key] = resolved_config[key]

    if step_override_path is not None:
        override_path = Path(step_override_path)
        if override_path.exists():
            with open(override_path, encoding="utf-8") as f:
                overrides = json.load(f)
            if isinstance(overrides, dict):
                step_cfg.update(overrides)

    cfg = CellDeconvStepConfig.model_validate(step_cfg)
    # Hierarchical (HiTIMED) tree is analyte-driven: default the analyte from the
    # project regulatory primary_analyte when the operator did not set one.
    if cfg.method == "hitimed" and not cfg.analyte:
        get_analyte = getattr(project, "get_primary_analyte", None)
        analyte = get_analyte() if callable(get_analyte) else None
        if analyte:
            cfg = cfg.model_copy(update={"analyte": str(analyte)})
    out = cfg.output_dir or f"{paths.output_base}/cell_fractions"
    return cfg, _all_sample_dirs(project), str(out)
