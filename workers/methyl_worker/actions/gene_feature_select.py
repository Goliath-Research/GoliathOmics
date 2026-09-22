"""CLI argv builder for pipeline.gene_feature_select (methyl-gene-feature-select)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import CliAction

GENE_FEATURE_SELECT_ARGV_MAP: Dict[str, str] = {
    "mapperDir": "--mapper-dir",
    "outputDir": "--output-dir",
}


def _resolve_gene_feature_select_cli_caps(
    resolved_config: Optional[Dict[str, Any]],
) -> tuple[Optional[int], Optional[float]]:
    if not isinstance(resolved_config, dict):
        return None, None
    max_features = resolved_config.get("max_features")
    if max_features is None:
        max_features = resolved_config.get("stability_gene_featurecuts_max_genes")
    target_ba = resolved_config.get("target_balanced_accuracy")
    if target_ba is None:
        target_ba = resolved_config.get("stability_target_balanced_accuracy")
    return (
        int(max_features) if max_features is not None else None,
        float(target_ba) if target_ba is not None else None,
    )


class GeneFeatureSelectCliAction(CliAction):
    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        resolved = payload.get("resolvedConfig") if isinstance(payload.get("resolvedConfig"), dict) else None
        max_features, target_ba = _resolve_gene_feature_select_cli_caps(resolved)
        payload.pop("tool", None)
        payload.pop("project", None)
        payload.pop("projectPath", None)
        cmd = super().build_argv(payload)
        if max_features is not None:
            cmd.extend(["--max-features", str(max_features)])
        if target_ba is not None:
            cmd.extend(["--target-ba", str(target_ba)])
        return cmd


from .collectors_registry import collector_gene_feature_select
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.gene_feature_select",
    action_cls=GeneFeatureSelectCliAction,
    argv_map=GENE_FEATURE_SELECT_ARGV_MAP,
    collector_factory=collector_gene_feature_select,
)
