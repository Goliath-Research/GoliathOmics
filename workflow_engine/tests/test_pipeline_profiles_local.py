"""Omics-owned pipeline profile loader (paired GoliathApp D6 decoupling)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOMAIN = REPO / "workflow_engine" / "domain"
sys.path.insert(0, str(DOMAIN))

from pipeline_profiles import (  # noqa: E402
    load_profile,
    seed_pipeline_scope_flags,
)


def test_load_samd_research_profile_has_modes_dir() -> None:
    profile = load_profile("samd_research")
    assert profile.get("pipelineProfile") == "samd_research"


def test_seed_pipeline_scope_flags_wgbs_pangenome() -> None:
    ctx = seed_pipeline_scope_flags(
        {"alignmentMode": "pangenome_wgbs", "actionConfig": {}},
        action_config={},
    )
    assert ctx.get("useWgbsPangenome") is True
    assert ctx.get("usePangenome") is True
