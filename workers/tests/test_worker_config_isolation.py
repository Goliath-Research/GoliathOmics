"""Guard tests for Universal Action Input Contract worker isolation."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

_WORKERS = Path(__file__).resolve().parents[1]
_DOMAIN = Path(__file__).resolve().parents[2] / "workflow_engine" / "domain"
for _p in (_WORKERS, _DOMAIN):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from methyl_worker.action_catalog import DEFAULT_PIPELINE_ARGV_MAP, find_catalog_entry
from methyl_worker.actions.base import CliAction
from methyl_worker.actions.enricher import ENRICHER_ARGV_MAP, EnricherCliAction


def _python_files_under(relative: str) -> list[Path]:
    root = Path(__file__).resolve().parents[2] / relative
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def test_worker_actions_do_not_import_resolve_for_project() -> None:
    actions_dir = Path(__file__).resolve().parents[1] / "methyl_worker" / "actions"
    offenders: list[str] = []
    for path in sorted(actions_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "methyl_utils.action_config_resolver":
                for alias in node.names:
                    if alias.name == "resolve_for_project":
                        offenders.append(f"{path.name}: import resolve_for_project")
            if isinstance(node, ast.Import) and any(
                alias.name == "resolve_for_project" for alias in node.names
            ):
                offenders.append(f"{path.name}: import resolve_for_project")
    assert not offenders, "worker actions must not import resolve_for_project:\n" + "\n".join(offenders)


def test_default_pipeline_argv_map_includes_resolved_config_carrier() -> None:
    mapping = dict(DEFAULT_PIPELINE_ARGV_MAP)
    assert mapping.get("resolvedConfigPath") == "--resolved-config"


def test_cli_action_materializes_resolved_config_file() -> None:
    entry = find_catalog_entry("pipeline.mapper")
    assert entry is not None
    action = CliAction(
        entry=entry,
        cli_tool="methyl-mapper",
        argv_map=dict(entry.argv_map),
    )
    cmd = action.build_argv(
        {
            "tool": "MethylMapper",
            "projectPath": "/work/projects/x/configs/project.json",
            "resolvedConfig": {"csv_pattern": "dmps-*-discovery.csv"},
        }
    )
    assert "--resolved-config" in cmd
    cfg_path = Path(cmd[cmd.index("--resolved-config") + 1])
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert payload["csv_pattern"] == "dmps-*-discovery.csv"
    finally:
        cfg_path.unlink(missing_ok=True)


def test_enricher_cli_action_merges_ppi_only_into_step_override() -> None:
    entry = find_catalog_entry("pipeline.enricher")
    assert entry is not None
    action = EnricherCliAction(entry=entry, cli_tool="methyl-enricher", argv_map=ENRICHER_ARGV_MAP)
    cmd = action.build_argv(
        {
            "tool": "MethylEnricher",
            "projectPath": "/work/projects/x/configs/project.json",
            "resolvedConfig": {"ppi_only": True},
        }
    )
    override_path = Path(cmd[cmd.index("--step-override") + 1])
    try:
        override = json.loads(override_path.read_text(encoding="utf-8"))
        assert override.get("ppi_only") is True
    finally:
        override_path.unlink(missing_ok=True)
    resolved_path = Path(cmd[cmd.index("--resolved-config") + 1])
    resolved_path.unlink(missing_ok=True)
