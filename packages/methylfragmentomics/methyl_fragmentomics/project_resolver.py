"""Resolve fragmentomics step config from pipeline project JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from .config import FragmentomicsStepConfig


def _all_sample_dirs(project) -> List[str]:  # noqa: ANN001
    seen: set[str] = set()
    paths: List[str] = []
    for _, group_paths in project.get_resolved_groups():
        for p in group_paths:
            if p not in seen:
                paths.append(p)
                seen.add(p)
    return paths


def resolve_fragmentomics_step_config(
    project_path: Union[str, Path],
    step_override_path: Optional[Union[str, Path]] = None,
) -> tuple[FragmentomicsStepConfig, List[str], str]:
    """
    Return (config, sample_dirs, output_dir) from profile actionConfig.fragmentomics.
    """
    project = load_project(project_path)
    paths = project.get_derived_paths()
    step_cfg: Dict[str, Any] = dict(resolve_for_project("fragmentomics", project))

    if step_override_path is not None:
        override_path = Path(step_override_path)
        if override_path.exists():
            with open(override_path, encoding="utf-8") as f:
                overrides = json.load(f)
            if isinstance(overrides, dict):
                step_cfg.update(overrides)

    cfg = FragmentomicsStepConfig.model_validate(step_cfg)
    if not cfg.genome_fasta:
        aq = resolve_for_project("alignment_qc", project)
        if isinstance(aq, dict) and aq.get("genome_fasta"):
            cfg = cfg.model_copy(update={"genome_fasta": aq["genome_fasta"]})

    out = cfg.output_dir or f"{paths.output_base}/fragmentomics"

    sample_dirs = _all_sample_dirs(project)
    return cfg, sample_dirs, str(out)
