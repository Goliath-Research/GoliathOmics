"""CLI argv builder for pipeline.detector (methyl-detector step-override contract)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..task_models.step_override_models import DetectorStepOverride
from .base import CliAction

# methyl-detector accepts --project, --group, --step-override, --centroid1-dir, --centroid2-dir.
# Per-chromosome scope uses step-override JSON (chromosome, contexts, fixed_dmp_panel, output_dir).
DETECTOR_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "project_path": "--project",
    "group": "--group",
    "centroid1Dir": "--centroid1-dir",
    "centroid2Dir": "--centroid2-dir",
    "stepOverride": "--step-override",
}


def merge_detector_step_override(input_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fold workflow scope fields into a typed methyl-detector step-override payload."""
    raw = input_json.get("stepOverride")
    if isinstance(raw, DetectorStepOverride):
        data = raw.model_dump(exclude_none=True, exclude_unset=True)
    elif isinstance(raw, dict):
        data = {k: v for k, v in raw.items() if v is not None}
    else:
        data = {}

    chromosome = input_json.get("chromosome")
    if chromosome not in (None, ""):
        data["chromosome"] = str(chromosome)

    context = input_json.get("context")
    if context not in (None, ""):
        if isinstance(context, list):
            data["contexts"] = [str(c) for c in context if str(c).strip()]
        else:
            data["contexts"] = [str(context)]

    panel = input_json.get("fixedDmpPanel")
    if panel not in (None, ""):
        data["fixed_dmp_panel"] = str(panel)

    output_dir = input_json.get("outputDir")
    if output_dir not in (None, ""):
        data["output_dir"] = str(output_dir)

    if not data:
        return None

    override = DetectorStepOverride.model_validate(data)
    payload = override.to_methyl_detector_payload()
    return payload or None


def resolve_detector_group(input_json: Dict[str, Any]) -> Optional[str]:
    """Map workflow comparison label to methyl-detector --group."""
    group = input_json.get("group")
    if group not in (None, ""):
        return str(group)
    comparison = input_json.get("comparison")
    if comparison not in (None, ""):
        return str(comparison)
    return None


class DetectorCliAction(CliAction):
    """Build methyl-detector argv without unsupported per-chromosome CLI flags."""

    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        override = merge_detector_step_override(payload)
        if override is not None:
            payload["stepOverride"] = override
        group = resolve_detector_group(payload)
        if group is not None:
            payload["group"] = group
        for drop_key in (
            "chromosome",
            "context",
            "comparison",
            "fixedDmpPanel",
            "outputDir",
            "addSamples",
            "removeSamples",
        ):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_detector
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.detector",
    action_cls=DetectorCliAction,
    argv_map=DETECTOR_ARGV_MAP,
    collector_factory=collector_detector,
)
