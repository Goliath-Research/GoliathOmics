from __future__ import annotations

from pathlib import Path

import pytest

from methyl_utils.pipeline_config import ProjectConfig


def _base_project(tmp_path: Path) -> dict:
    samples = tmp_path / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    return {
        "project_name": "qc_demo",
        "output_base": str(tmp_path / "out"),
        "samples_base_path": str(samples),
        "chromosomes": ["1"],
        "contexts": ["CG"],
        "control": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": []}],
        },
        "disease": {
            "label": "cancer",
            "groups": [{"label": "all", "sample_paths": []}],
        },
        "comparisons": "control_vs_each_disease",
    }


def test_global_sample_qc_excludes_samples_without_h5_and_writes_artifacts(tmp_path: Path):
    cfg = _base_project(tmp_path)
    samples_base = Path(cfg["samples_base_path"])
    keep_control = samples_base / "ctrl_keep"
    drop_control = samples_base / "ctrl_drop"
    keep_disease = samples_base / "dis_keep"
    keep_control.mkdir(parents=True, exist_ok=True)
    drop_control.mkdir(parents=True, exist_ok=True)
    keep_disease.mkdir(parents=True, exist_ok=True)
    (keep_control / "1-CG.h5").write_bytes(b"")
    (keep_disease / "1-CG.h5").write_bytes(b"")

    control_csv = tmp_path / "control.csv"
    disease_csv = tmp_path / "disease.csv"
    control_csv.write_text("sample\nctrl_keep\nctrl_drop\n", encoding="utf-8")
    disease_csv.write_text("sample\ndis_keep\n", encoding="utf-8")
    cfg["control"]["groups"][0]["sample_paths"] = [str(control_csv)]
    cfg["disease"]["groups"][0]["sample_paths"] = [str(disease_csv)]
    project = ProjectConfig.model_validate(cfg)

    resolved = project.get_resolved_groups()
    assert len(resolved) == 2
    assert resolved[0][0] == "all"
    assert str(keep_control) in resolved[0][1]
    assert str(drop_control) not in resolved[0][1]

    qc_dir = Path(project.get_project_root()) / "sample_qc"
    report_path = qc_dir / "sample_qc_report.csv"
    eligible_path = qc_dir / "eligible_samples.txt"
    ineligible_path = qc_dir / "ineligible_samples.txt"
    assert report_path.is_file()
    assert eligible_path.is_file()
    assert ineligible_path.is_file()
    assert "ctrl_drop" in ineligible_path.read_text(encoding="utf-8")
    for path in (report_path, eligible_path, ineligible_path):
        assert path.stat().st_mode & 0o222, path


def test_global_sample_qc_fails_when_group_becomes_empty(tmp_path: Path):
    cfg = _base_project(tmp_path)
    samples_base = Path(cfg["samples_base_path"])
    ctrl_bad = samples_base / "ctrl_bad"
    dis_keep = samples_base / "dis_keep"
    ctrl_bad.mkdir(parents=True, exist_ok=True)
    dis_keep.mkdir(parents=True, exist_ok=True)
    (dis_keep / "1-CG.h5").write_bytes(b"")

    control_csv = tmp_path / "control.csv"
    disease_csv = tmp_path / "disease.csv"
    control_csv.write_text("sample\nctrl_bad\n", encoding="utf-8")
    disease_csv.write_text("sample\ndis_keep\n", encoding="utf-8")
    cfg["control"]["groups"][0]["sample_paths"] = [str(control_csv)]
    cfg["disease"]["groups"][0]["sample_paths"] = [str(disease_csv)]
    project = ProjectConfig.model_validate(cfg)

    with pytest.raises(ValueError, match="removed all samples"):
        project.get_resolved_groups()
