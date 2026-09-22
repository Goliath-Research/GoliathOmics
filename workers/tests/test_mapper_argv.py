"""Tests for pipeline.mapper CLI argv synthesis."""

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
from methyl_worker.actions.mapper import MAPPER_ARGV_MAP, MapperCliAction, merge_mapper_step_override


def test_merge_mapper_step_override_merges_resolved_mapper_slice() -> None:
    merged = merge_mapper_step_override(
        {
            "resolvedConfig": {
                "mapper": {
                    "csv_pattern": "dmps-*-discovery.csv",
                    "csv_filename_pattern": "dmps-*-selected.csv",
                }
            },
        }
    )
    assert merged is not None
    assert merged["csv_pattern"] == "dmps-*-discovery.csv"
    assert merged["csv_filename_pattern"] == "dmps-*-selected.csv"


def test_mapper_build_argv_omits_comparison_flag() -> None:
    entry = find_catalog_entry("pipeline.mapper")
    assert entry is not None
    action = MapperCliAction(entry=entry, cli_tool="methyl-mapper", argv_map=MAPPER_ARGV_MAP)
    cmd = action.build_argv(
        {
            "tool": "MethylMapper",
            "projectPath": "/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json",
            "group": "PCa",
            "comparison": "PCa",
        }
    )
    assert cmd[0] == "methyl-mapper"
    assert "--comparison" not in cmd
    assert "--group" in cmd
    assert "PCa" in cmd
    assert "--project" in cmd

    if "--step-override" in cmd:
        override_path = Path(cmd[cmd.index("--step-override") + 1])
        try:
            payload = json.loads(override_path.read_text(encoding="utf-8"))
            assert "comparison" not in payload
        finally:
            override_path.unlink(missing_ok=True)
