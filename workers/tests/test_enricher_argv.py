"""Tests for pipeline.enricher CLI argv synthesis."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_WORKERS = Path(__file__).resolve().parents[1]
_DOMAIN = Path(__file__).resolve().parents[2] / "workflow_engine" / "domain"
for _p in (_WORKERS, _DOMAIN):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from methyl_worker.action_catalog import find_catalog_entry
from methyl_worker.actions.enricher import ENRICHER_ARGV_MAP, EnricherCliAction, merge_enricher_step_override


def test_merge_enricher_step_override_merges_resolved_slice() -> None:
    merged = merge_enricher_step_override(
        {
            "resolvedConfig": {"ppi_only": True, "modules": False},
        }
    )
    assert merged is not None
    assert merged["ppi_only"] is True
    assert merged["modules"] is False


def test_enricher_build_argv_passes_resolved_config_and_step_override() -> None:
    entry = find_catalog_entry("pipeline.enricher")
    assert entry is not None
    action = EnricherCliAction(entry=entry, cli_tool="methyl-enricher", argv_map=ENRICHER_ARGV_MAP)
    cmd = action.build_argv(
        {
            "tool": "MethylEnricher",
            "projectPath": "/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json",
            "comparison": "PCa",
            "resolvedConfig": {"ppi_only": True},
        }
    )
    assert cmd[0] == "methyl-enricher"
    assert "--resolved-config" in cmd
    assert "--step-override" in cmd
    resolved_path = Path(cmd[cmd.index("--resolved-config") + 1])
    override_path = Path(cmd[cmd.index("--step-override") + 1])
    try:
        resolved_payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        override_payload = json.loads(override_path.read_text(encoding="utf-8"))
        assert resolved_payload["ppi_only"] is True
        assert override_payload["ppi_only"] is True
    finally:
        resolved_path.unlink(missing_ok=True)
        override_path.unlink(missing_ok=True)
