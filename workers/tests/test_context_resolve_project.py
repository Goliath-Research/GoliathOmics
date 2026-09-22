"""Tests for context.resolve_project in-process handler."""

from __future__ import annotations

import sys
from pathlib import Path

from local_project_support import project_with_local_samples

_WORKERS = Path(__file__).resolve().parents[1]
if str(_WORKERS) not in sys.path:
    sys.path.insert(0, str(_WORKERS))

from methyl_worker import handlers
from methyl_worker.depends import call_in_process_handler
from methyl_worker.task_models.context_models import ResolveProjectTaskInput
from methyl_worker.task_models.runtime_models import TaskRuntimeContext


def test_handle_context_resolve_project_smoke(tmp_path: Path) -> None:
    project_path = (
        Path(__file__).resolve().parents[2]
        / "workflow_engine"
        / "domain"
        / "checks"
        / "buffy_healthy_vs_pca"
        / "configs"
        / "project_Buffy_healthy_vs_PCa.json"
    )
    if not project_path.is_file():
        import pytest

        pytest.skip(f"smoke project missing: {project_path}")

    local = project_with_local_samples(project_path, tmp_path)
    out = call_in_process_handler(
        handlers._handle_context_resolve_project,
        "context.resolve-project",
        "context.resolve_project",
        ResolveProjectTaskInput(tool="ContextResolveProject", projectPath=str(local)),
        TaskRuntimeContext(),
    )
    assert out.resolvedProject["$type"] == "ResolvedProject"
    assert out.resolvedProject["comparisons"]
