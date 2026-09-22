"""Tests for info_measures runner skip behavior."""

from __future__ import annotations

from pathlib import Path

from methyl_infotheory.config import InfoTheoryStepConfig
from methyl_infotheory.core.runner import run_info_measures_for_project


def test_runner_skips_when_no_pattern_sidecars(tmp_path):
    sample_dir = tmp_path / "sample_a"
    sample_dir.mkdir()
    out = tmp_path / "out"
    summary = run_info_measures_for_project(
        [("sample_a", str(sample_dir), "healthy")],
        out,
        InfoTheoryStepConfig(),
        project_chromosomes=["1"],
    )
    assert summary["status"] == "skipped"
    assert summary["output_csv"] is None
    assert (out / "readlevel_measures.manifest.json").is_file()
