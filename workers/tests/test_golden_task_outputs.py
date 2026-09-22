"""Validate committed golden I/O fixtures against catalog Pydantic models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_worker.task_schema_registry import list_task_schema_specs

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _golden_path(action_name: str, direction: str) -> Path:
    safe = action_name.replace(".", "_")
    return GOLDEN_DIR / f"{safe}.{direction}.json"


@pytest.mark.parametrize("spec", list_task_schema_specs(), ids=lambda s: s.action_name)
def test_golden_input_fixture(spec) -> None:
    path = _golden_path(spec.action_name, "input")
    assert path.is_file(), f"missing golden input: {path} (run scripts/generate_golden_fixtures.py)"
    data = json.loads(path.read_text(encoding="utf-8"))
    spec.load_input_model().model_validate(data)


@pytest.mark.parametrize("spec", list_task_schema_specs(), ids=lambda s: s.action_name)
def test_golden_output_fixture(spec) -> None:
    path = _golden_path(spec.action_name, "output")
    assert path.is_file(), f"missing golden output: {path} (run scripts/generate_golden_fixtures.py)"
    data = json.loads(path.read_text(encoding="utf-8"))
    spec.load_output_model().model_validate(data)
