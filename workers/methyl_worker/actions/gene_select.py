"""CLI argv builder for pipeline.gene_select (methyl-gene-select)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from methyl_gene_select.caps import resolve_gene_featurecuts_caps

from .base import CliAction

GENE_SELECT_ARGV_MAP: Dict[str, str] = {
    "project": "--project",
    "projectPath": "--project",
    "runDir": "--run-dir",
    "maxGenes": "--max-genes",
    "maxDmps": "--max-dmps",
    "biomarkerFilter": "--biomarker-filter",
}


def default_run_dir_for_project(project: str) -> str:
    """
    Infer MC run directory from a project path without touching the filesystem.

    Project configs are files (e.g. ``.../configs/project.json``); iteration artifacts
    live alongside them in the parent directory or an explicit ``runDir``.
    """
    project_path = Path(str(project))
    if project_path.suffix:
        return str(project_path.parent)
    return str(project_path)


class GeneSelectCliAction(CliAction):
    def build_argv(self, input_json: Dict[str, Any]) -> List[str]:
        payload: Dict[str, Any] = dict(input_json)
        biomarker = payload.pop("biomarkerFilter", None)
        project = payload.get("project") or payload.get("projectPath")
        if project and not payload.get("runDir"):
            payload["runDir"] = default_run_dir_for_project(str(project))
        max_genes, max_dmps = resolve_gene_featurecuts_caps(
            max_genes=None,
            max_dmps=None,
            resolved_config=payload.get("resolvedConfig")
            if isinstance(payload.get("resolvedConfig"), dict)
            else None,
            run_dir=payload.get("runDir"),
        )
        if max_genes is not None:
            payload["maxGenes"] = max_genes
        if max_dmps is not None:
            payload["maxDmps"] = max_dmps
        cmd = super().build_argv(payload)
        if biomarker:
            cmd.append("--biomarker-filter")
        return cmd


from .collectors_registry import collector_gene_select
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.gene_select",
    action_cls=GeneSelectCliAction,
    argv_map=GENE_SELECT_ARGV_MAP,
    collector_factory=collector_gene_select,
)
