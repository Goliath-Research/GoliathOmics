"""Unit tests for cfg.domain_program registration helper."""

from __future__ import annotations

import json
from pathlib import Path

from workflow_engine.ops.domain_program_registry import load_program_document

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_mc_fixture_program_documents_load() -> None:
    fixture = REPO_ROOT / "workflow_engine/domain/fixtures/mc_stability.program.json"
    doc = load_program_document(fixture)
    assert doc["name"] == "MC_Stability"
    assert "programVersion" in doc
    json.dumps(doc)
