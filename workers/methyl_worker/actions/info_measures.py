"""CLI argv builder for pipeline.info_measures."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import CliAction


INFO_MEASURES_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "outputDir": "--output-dir",
    "stepOverride": "--step-override",
    "resolvedConfigPath": "--resolved-config",
}


class InfoMeasuresCliAction(CliAction):
    """Build methyl-infotheory argv."""

    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        for drop_key in ("chromosome", "context", "comparison", "group"):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_info_measures
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.info_measures",
    action_cls=InfoMeasuresCliAction,
    argv_map=INFO_MEASURES_ARGV_MAP,
    collector_factory=collector_info_measures,
)
