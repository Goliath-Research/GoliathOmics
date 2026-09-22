"""CLI argv builder for pipeline.mhb_mhl."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import CliAction


MHB_MHL_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "outputDir": "--output-dir",
    "stepOverride": "--step-override",
    "resolvedConfigPath": "--resolved-config",
}


class MhbMhlCliAction(CliAction):
    """Build methyl-mhl argv."""

    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        for drop_key in ("chromosome", "context", "comparison", "group"):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_mhb_mhl
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.mhb_mhl",
    action_cls=MhbMhlCliAction,
    argv_map=MHB_MHL_ARGV_MAP,
    collector_factory=collector_mhb_mhl,
)
