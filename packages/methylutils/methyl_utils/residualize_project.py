"""Resolve residualize / confounder-score step config from a pipeline project."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union

from pydantic import BaseModel

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

T = TypeVar("T", bound=BaseModel)


def all_sample_dirs(project) -> List[Tuple[str, str]]:  # noqa: ANN001
    seen: set[str] = set()
    out: List[Tuple[str, str]] = []
    for _label, group_paths in project.get_resolved_groups():
        for p in group_paths:
            if p in seen:
                continue
            seen.add(p)
            out.append((Path(p).name, p))
    return out


def resolve_named_step_config(
    action_key: str,
    model_cls: Type[T],
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
    resolved_config: Optional[Dict[str, Any]] = None,
    *,
    default_output_subdir: str,
) -> Tuple[T, List[Tuple[str, str]], str]:
    project = load_project(project_path)
    paths = project.get_derived_paths()
    # Worker path: baked ``resolved_config`` is the sole tool-parameter source.
    # Do not re-merge site/profile via resolve_for_project (nulls stay null).
    if isinstance(resolved_config, dict):
        nested = resolved_config.get(action_key)
        if isinstance(nested, dict):
            src = nested
        else:
            src = resolved_config
        step_cfg = {k: src[k] for k in src if k in model_cls.model_fields}
    else:
        step_cfg = dict(resolve_for_project(action_key, project))
    if step_override_path is not None:
        override_path = Path(step_override_path)
        if override_path.exists():
            with open(override_path, encoding="utf-8") as f:
                overrides = json.load(f)
            if isinstance(overrides, dict):
                step_cfg.update(overrides)
    cfg = model_cls.model_validate(step_cfg)
    out = getattr(cfg, "output_dir", None) or f"{paths.output_base}/{default_output_subdir}"
    return cfg, all_sample_dirs(project), str(out)
