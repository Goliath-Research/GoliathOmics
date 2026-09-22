"""Tier-1 contract-drift guards for task schemas and task-input/config boundary.

These promote CI-only checks into the PR pytest gate: change a task Pydantic
model without regenerating ``schemas/tasks/*.schema.json`` (or leak a config knob
onto a task wire payload) and the suite fails here rather than staying green.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def test_committed_task_schemas_match_pydantic_models():
    """schemas/tasks/*.schema.json must match the current task models."""
    from methyl_worker.task_schema_export import check_task_schema_drift

    drift = check_task_schema_drift()
    assert drift == [], "Task schema drift (run methyl-export-task-schemas):\n" + "\n".join(
        drift
    )


def test_task_schema_export_is_deterministic(tmp_path: Path):
    from methyl_worker.task_schema_export import (
        export_all_task_schemas,
        list_task_schema_specs,
    )

    root = tmp_path / "tasks"
    export_all_task_schemas(schemas_root=root, write=True)
    first = {p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.schema.json"))}
    export_all_task_schemas(schemas_root=root, write=True)
    second = {p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.schema.json"))}
    assert first == second
    # input + output artifact per spec.
    assert len(list(root.rglob("*.schema.json"))) == len(list_task_schema_specs()) * 2


def test_task_schema_drift_detects_stale_artifact(tmp_path: Path):
    from methyl_worker.task_schema_export import (
        check_task_schema_drift,
        export_all_task_schemas,
    )

    root = tmp_path / "tasks"
    export_all_task_schemas(schemas_root=root, write=True)
    target = next(root.rglob("*.schema.json"))
    target.write_text('{"stale": true}\n', encoding="utf-8")
    drift = check_task_schema_drift(schemas_root=root)
    assert any("stale schema artifact" in msg for msg in drift)


def test_task_input_config_boundary_holds():
    """Task wire fields must not overlap resolvable actionConfig keys."""
    from check_task_input_config_boundary import check_catalog

    errors = check_catalog()
    assert errors == [], "Task input / actionConfig boundary violations:\n" + "\n".join(
        errors
    )
