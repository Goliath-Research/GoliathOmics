"""Resolve mhb_mhl config and sample paths from a pipeline project."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from .config import MhbMhlStepConfig


def _all_sample_dirs(project) -> List[Tuple[str, str, str]]:  # noqa: ANN001
    seen: set[str] = set()
    out: List[Tuple[str, str, str]] = []
    for label, group_paths in project.get_resolved_groups():
        for p in group_paths:
            if p in seen:
                continue
            seen.add(p)
            out.append((Path(p).name, p, str(label)))
    return out


def resolve_mhb_mhl_step_config(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    resolved_config: Optional[Dict[str, Any]] = None,
) -> Tuple[MhbMhlStepConfig, List[Tuple[str, str, str]], str]:
    project = load_project(project_path)
    paths = project.get_derived_paths()
    step_cfg: Dict[str, Any] = dict(resolve_for_project("mhb_mhl", project))
    if isinstance(resolved_config, dict):
        nested = resolved_config.get("mhb_mhl")
        if isinstance(nested, dict):
            step_cfg.update(nested)
        else:
            for key in MhbMhlStepConfig.model_fields:
                if resolved_config.get(key) is not None:
                    step_cfg[key] = resolved_config[key]

    if step_override_path is not None:
        override_path = Path(step_override_path)
        if override_path.exists():
            with open(override_path, encoding="utf-8") as f:
                overrides = json.load(f)
            if isinstance(overrides, dict):
                step_cfg.update(overrides)

    cfg = MhbMhlStepConfig.model_validate(step_cfg)
    out = cfg.output_dir or f"{paths.output_base}/mhb_mhl"
    return cfg, _all_sample_dirs(project), str(out)
