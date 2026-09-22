"""CLI argv builder for pipeline.residualize_fit."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import CliAction


RESIDUALIZE_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "outputDir": "--output-dir",
    "stepOverride": "--step-override",
    "resolvedConfigPath": "--resolved-config",
}


class ResidualizeFitCliAction(CliAction):
    """Build methyl-residualize-fit argv."""

    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        for drop_key in ("chromosome", "context", "comparison", "group"):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_residualize_fit
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.residualize_fit",
    action_cls=ResidualizeFitCliAction,
    argv_map=RESIDUALIZE_ARGV_MAP,
    collector_factory=collector_residualize_fit,
)
