"""CLI argv builder for pipeline.enricher (methyl-enricher step-override contract)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..task_models.step_override_models import EnricherStepOverride
from .base import CliAction

ENRICHER_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "project_path": "--project",
    "comparison": "--comparison",
    "outputDir": "--output-dir",
    "stepOverride": "--step-override",
    "resolvedConfigPath": "--resolved-config",
}


def merge_enricher_step_override(input_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fold workflow resolvedConfig.enricher slice into methyl-enricher --step-override JSON."""
    raw = input_json.get("stepOverride")
    if isinstance(raw, EnricherStepOverride):
        data = raw.model_dump(exclude_none=True, exclude_unset=True)
    elif isinstance(raw, dict):
        data = {k: v for k, v in raw.items() if v is not None}
    else:
        data = {}

    resolved = input_json.get("resolvedConfig")
    if isinstance(resolved, dict):
        enricher_cfg = resolved.get("enricher")
        if isinstance(enricher_cfg, dict):
            for key, val in enricher_cfg.items():
                if val is not None and key not in data:
                    data[key] = val
        else:
            for key, val in resolved.items():
                if val is not None and key not in data:
                    data[key] = val

    output_dir = input_json.get("outputDir")
    if output_dir not in (None, ""):
        data["output_dir"] = str(output_dir)

    if not data:
        return None

    override = EnricherStepOverride.model_validate(data)
    payload = override.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    return payload or None


class EnricherCliAction(CliAction):
    """Build methyl-enricher argv with resolvedConfig merged into step-override."""

    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        override = merge_enricher_step_override(payload)
        if override is not None:
            payload["stepOverride"] = override
        for drop_key in (
            "chromosome",
            "context",
            "group",
            "centroid1Dir",
            "centroid2Dir",
            "addSamples",
            "removeSamples",
        ):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_enricher
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.enricher",
    action_cls=EnricherCliAction,
    argv_map=ENRICHER_ARGV_MAP,
    collector_factory=collector_enricher,
)
