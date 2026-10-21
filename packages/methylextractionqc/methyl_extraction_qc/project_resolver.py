"""Resolve extraction QC config from a pipeline project."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from methyl_utils import load_project
from methyl_utils.action_config_resolver import (
    deep_merge,
    load_resolved_config,
    resolve_for_project,
)

from .models.config import (
    DEFAULT_EXPECTED_CHROMOSOMES,
    ExtractionQCConfig,
    ExtractionQCGuardrailConfig,
)


def _optional_path(step_cfg: Mapping[str, Any], key: str) -> Optional[str]:
    value = step_cfg.get(key)
    if value in (None, ""):
        return None
    return str(value)


def sample_extraction_overlay(sample_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Sparse extraction_qc overlay carried on one foreach sample record.

    Instance bake (``resolvedConfig``) is the merged site/profile/instance window.
    A sample may pin ``guardrails`` or ``actionConfig.extraction_qc`` on top.
    """
    if not isinstance(sample_context, Mapping):
        return {}
    overlay: Dict[str, Any] = {}
    action = sample_context.get("actionConfig")
    if isinstance(action, Mapping):
        extraction = action.get("extraction_qc")
        if isinstance(extraction, Mapping):
            overlay = deep_merge(overlay, dict(extraction))
    guardrails = sample_context.get("guardrails")
    if isinstance(guardrails, Mapping):
        overlay = deep_merge(overlay, {"guardrails": dict(guardrails)})
    return overlay


def _chromosomes_from_step(
    step_cfg: Mapping[str, Any],
    project_chromosomes: Optional[List[str]],
) -> List[str]:
    raw = step_cfg.get("expected_chromosomes")
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw]
    if project_chromosomes:
        return list(project_chromosomes)
    return list(DEFAULT_EXPECTED_CHROMOSOMES)


def _extraction_qc_config_from_step(
    step_cfg: Mapping[str, Any],
    *,
    sample_paths: List[str],
    project_chromosomes: Optional[List[str]],
) -> ExtractionQCConfig:
    guardrail_cfg = ExtractionQCGuardrailConfig()
    raw_guardrails = step_cfg.get("guardrails")
    if isinstance(raw_guardrails, dict):
        guardrail_cfg = ExtractionQCGuardrailConfig.model_validate(raw_guardrails)

    return ExtractionQCConfig(
        guardrails=guardrail_cfg,
        expected_chromosomes=_chromosomes_from_step(step_cfg, project_chromosomes),
        sample_paths=list(sample_paths),
        target_panel_bed=_optional_path(step_cfg, "target_panel_bed"),
        pos_control_bed=_optional_path(step_cfg, "pos_control_bed"),
        neg_control_bed=_optional_path(step_cfg, "neg_control_bed"),
    )


def extraction_qc_window(
    *,
    resolved_config: Optional[Mapping[str, Any]] = None,
    sample_context: Optional[Mapping[str, Any]] = None,
) -> Optional[ExtractionQCConfig]:
    """Coerce a baked instance window plus a sample overlay. No project, no site env."""
    merged: Dict[str, Any] = {}
    if isinstance(resolved_config, Mapping):
        merged = load_resolved_config("extraction_qc", resolved_config=dict(resolved_config))
    overlay = sample_extraction_overlay(sample_context)
    if overlay:
        merged = deep_merge(merged, overlay)
    if not merged:
        return None
    return _extraction_qc_config_from_step(
        merged,
        sample_paths=[],
        project_chromosomes=None,
    )


def resolve_extraction_qc_config(
    project_path: Union[str, Path],
    *,
    sample_paths: Optional[List[str]] = None,
    resolved_config: Optional[Mapping[str, Any]] = None,
    sample_context: Optional[Mapping[str, Any]] = None,
) -> ExtractionQCConfig:
    project = load_project(project_path)
    # Baked task resolvedConfig is the instance window. Do not re-read site/profile
    # from the worker environment when it is present (workers hold no DB secrets
    # and must not substitute the published default for an instance override).
    if isinstance(resolved_config, Mapping):
        step_cfg: Dict[str, Any] = load_resolved_config(
            "extraction_qc", resolved_config=dict(resolved_config)
        )
    else:
        step_cfg = resolve_for_project("extraction_qc", project)
    overlay = sample_extraction_overlay(sample_context)
    if overlay:
        step_cfg = deep_merge(dict(step_cfg or {}), overlay)

    raw_chroms = getattr(project, "chromosomes", None)
    project_chromosomes = [str(item) for item in raw_chroms] if raw_chroms else None

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
        raw_paths = step_cfg.get("sample_paths") if step_cfg else None
        if isinstance(raw_paths, list) and raw_paths:
            paths = [str(item) for item in raw_paths]

    return _extraction_qc_config_from_step(
        step_cfg or {},
        sample_paths=paths,
        project_chromosomes=project_chromosomes,
    )
