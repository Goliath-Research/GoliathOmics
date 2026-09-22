"""Tests for ResolvedProject materialization."""

from __future__ import annotations

from pathlib import Path

from methyl_domain.helpers import build_resolved_project
from local_project_support import project_with_local_samples


def test_build_resolved_project_from_smoke_project(tmp_path: Path) -> None:
    project_path = (
        Path(__file__).resolve().parents[3]
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
    resolved = build_resolved_project(local)
    assert resolved.projectPath.endswith("project_Buffy_healthy_vs_PCa.local.json")
    assert resolved.groups
    assert resolved.comparisons
    cmp = resolved.comparisons[0]
    assert cmp.detectOutDir
    assert cmp.mapperOutDir
    assert cmp.enricherOutDir
    assert isinstance(cmp.controlSamplePaths, list)
    assert isinstance(cmp.diseaseSamplePaths, list)
