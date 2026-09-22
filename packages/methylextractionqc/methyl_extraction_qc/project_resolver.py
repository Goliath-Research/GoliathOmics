"""Resolve extraction QC config from a pipeline project."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import resolve_for_project

from .models.config import DEFAULT_EXPECTED_CHROMOSOMES, ExtractionQCConfig, ExtractionQCGuardrailConfig


def resolve_extraction_qc_config(
    project_path: Union[str, Path],
    *,
    sample_paths: Optional[List[str]] = None,
) -> ExtractionQCConfig:
    project = load_project(project_path)
    step_cfg = resolve_for_project("extraction_qc", project)
    guardrail_cfg = ExtractionQCGuardrailConfig()
    if isinstance(step_cfg.get("guardrails"), dict):
        guardrail_cfg = ExtractionQCGuardrailConfig.model_validate(step_cfg["guardrails"])

    chromosomes = list(getattr(project, "chromosomes", None) or DEFAULT_EXPECTED_CHROMOSOMES)
    if step_cfg.get("expected_chromosomes"):
        chromosomes = [str(item) for item in step_cfg["expected_chromosomes"]]

    # Per-sample worker QC always passes sample_paths. Do not call
    # get_resolved_groups() in that case: it rewrites the cohort
    # sample_qc_report.csv as a side effect and fails on root-owned NFS files.
    if sample_paths is not None:
        paths = list(sample_paths)
    else:
        default_paths: List[str] = []
        for _, group_paths in project.get_resolved_groups():
            default_paths.extend(group_paths)
        paths = default_paths
        if step_cfg.get("sample_paths"):
            paths = [str(item) for item in step_cfg["sample_paths"]]

    return ExtractionQCConfig(
        guardrails=guardrail_cfg,
        expected_chromosomes=chromosomes,
        sample_paths=paths,
    )
