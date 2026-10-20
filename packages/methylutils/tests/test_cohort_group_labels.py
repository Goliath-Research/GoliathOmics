"""Cohort labels are copied without H5 QC; empty groups are a distinct error."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from methyl_utils.pipeline_config import ProjectConfig


def _project(tmp_path: Path, *, control_paths: list[str], disease_paths: list[str]) -> ProjectConfig:
    samples = tmp_path / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    payload = {
        "project_name": "prep_only",
        "output_base": str(tmp_path / "out"),
        "samples_base_path": str(samples),
        "chromosomes": ["1", "21"],
        "contexts": ["CG"],
        "control": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": control_paths}],
        },
        "disease": {
            "label": "cancer",
            "groups": [{"label": "PCa", "sample_paths": disease_paths}],
        },
        "comparisons": "control_vs_each_disease",
    }
    return cast(ProjectConfig, ProjectConfig.model_validate(payload))


def test_cohort_group_labels_skip_h5_checks(tmp_path: Path) -> None:
    project = _project(tmp_path, control_paths=[], disease_paths=[])
    assert project.cohort_group_labels() == ["all", "PCa"]


def test_declared_empty_groups_are_not_reported_as_qc_removal(tmp_path: Path) -> None:
    project = _project(tmp_path, control_paths=[], disease_paths=[])
    with pytest.raises(ValueError, match="Cohort group has no samples: control:all, disease:PCa") as exc:
        project.get_resolved_groups()
    assert "removed all samples" not in str(exc.value)


def test_ineligible_samples_keep_the_qc_error(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    bad = samples / "ctrl_bad"
    good = samples / "dis_keep"
    bad.mkdir(parents=True, exist_ok=True)
    good.mkdir(parents=True, exist_ok=True)
    (good / "1-CG.h5").write_bytes(b"")
    (good / "21-CG.h5").write_bytes(b"")
    control_csv = tmp_path / "control.csv"
    disease_csv = tmp_path / "disease.csv"
    control_csv.write_text("sample\nctrl_bad\n", encoding="utf-8")
    disease_csv.write_text("sample\ndis_keep\n", encoding="utf-8")
    project = _project(
        tmp_path,
        control_paths=[str(control_csv)],
        disease_paths=[str(disease_csv)],
    )
    with pytest.raises(ValueError, match="removed all samples from cohort\\(s\\): control:all") as exc:
        project.get_resolved_groups()
    assert "Cohort group has no samples" not in str(exc.value)
