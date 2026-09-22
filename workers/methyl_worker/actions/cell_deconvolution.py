"""CLI argv builder for pipeline.cell_deconvolution."""

from __future__ import annotations

from typing import Any, Dict, List

from .base import CliAction


CELL_DECONV_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "outputDir": "--output-dir",
    "stepOverride": "--step-override",
    "resolvedConfigPath": "--resolved-config",
}


class CellDeconvolutionCliAction(CliAction):
    """Build methyl-cell-deconv argv."""

    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        for drop_key in ("chromosome", "context", "comparison", "group"):
            payload.pop(drop_key, None)
        return super().build_argv(payload)


from .collectors_registry import collector_cell_deconvolution
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.cell_deconvolution",
    action_cls=CellDeconvolutionCliAction,
    argv_map=CELL_DECONV_ARGV_MAP,
    collector_factory=collector_cell_deconvolution,
)
