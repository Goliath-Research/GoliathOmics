"""CLI argv builder for pipeline.methylation_confounder_scores."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import CliAction


CONFOUNDER_SCORES_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "outputDir": "--output-dir",
    "stepOverride": "--step-override",
    "resolvedConfigPath": "--resolved-config",
}


class ConfounderScoresCliAction(CliAction):
    """Build methyl-confounder-scores argv."""

    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        for drop_key in ("chromosome", "context", "comparison", "group"):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_confounder_scores
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.methylation_confounder_scores",
    action_cls=ConfounderScoresCliAction,
    argv_map=CONFOUNDER_SCORES_ARGV_MAP,
    collector_factory=collector_confounder_scores,
)
