"""Operator enqueue script stays on the platform study-start queue."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "workflow_engine" / "sql_pg" / "enqueue_sample_prep_test_start.sql"


def test_enqueue_sample_prep_test_start_uses_platform_queue() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "portal.sp_request_study_start" in text
    assert "portal.sp_preview_study_start" in text
    assert "'sample_prep'" in text
    assert "cfg.study_start_request" in text
    assert "\\if :{?study_row_id}" in text
    assert "\\if :{?project_path}" in text
