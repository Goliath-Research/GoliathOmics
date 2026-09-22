"""CLI argv builder for pipeline.dmp_select (methyl-dmp-select)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..task_models.step_override_models import DmpSelectStepOverride
from .base import CliAction
from .detector import resolve_detector_group

DMP_SELECT_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "group": "--group",
    "chromosome": "--chromosome",
    "discoveryCsv": "--discovery-csv",
    "outputDir": "--output-dir",
    "stepOverride": "--step-override",
}


def merge_dmp_select_step_override(input_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fold workflow scope and resolved dmp_selection config into step-override JSON."""
    raw = input_json.get("stepOverride")
    if isinstance(raw, DmpSelectStepOverride):
        data = raw.model_dump(exclude_none=True, exclude_unset=True)
    elif isinstance(raw, dict):
        data = {k: v for k, v in raw.items() if v is not None}
    else:
        data = {}

    resolved = input_json.get("resolvedConfig")
    if isinstance(resolved, dict):
        for key in (
            "classifier_dmp_selection",
            "target_balanced_accuracy",
            "min_core_dmps",
            "classifier_export_margin_pct",
            "classifier_export_margin_abs",
            "classifier_export_max_dmps",
            "fail_if_below_target",
        ):
            if resolved.get(key) is not None and key not in data:
                data[key] = resolved[key]

    output_dir = input_json.get("outputDir")
    if output_dir not in (None, ""):
        data["output_dir"] = str(output_dir)

    if not data:
        return None

    override = DmpSelectStepOverride.model_validate(data)
    payload = override.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    return payload or None


class DmpSelectCliAction(CliAction):
    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        override = merge_dmp_select_step_override(payload)
        if override is not None:
            payload["stepOverride"] = override
        group = resolve_detector_group(payload)
        if group is not None:
            payload["group"] = group
        for drop_key in (
            "context",
            "comparison",
            "fixedDmpPanel",
            "addSamples",
            "removeSamples",
        ):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_dmp_select
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.dmp_select",
    action_cls=DmpSelectCliAction,
    argv_map=DMP_SELECT_ARGV_MAP,
    collector_factory=collector_dmp_select,
)
