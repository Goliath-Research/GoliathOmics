"""Disease groups with nested ``stages`` expand to typed leaf labels."""

import json
from pathlib import Path

import pytest

from methyl_utils import load_project


def test_disease_stages_expand_labels(tmp_path: Path):
    a_csv = str((tmp_path / "a.csv").resolve())
    b_csv = str((tmp_path / "b.csv").resolve())
    (tmp_path / "a.csv").write_text("sample\ns1\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("sample\ns2\n", encoding="utf-8")
    p = tmp_path / "project.json"
    p.write_text(
        json.dumps(
            {
                "project_name": "t",
                "output_base": str((tmp_path / "out").resolve()),
                "samples_base_path": str(tmp_path.resolve()),
                "controls": {
                    "label": "h",
                    "groups": [{"label": "pool", "sample_paths": [a_csv]}],
                },
                "diseases": {
                    "label": "c",
                    "groups": [
                        {
                            "label": "pca",
                            "stages": [
                                {"label": "I", "sample_paths": [a_csv]},
                                {"label": "II", "sample_paths": [b_csv]},
                            ],
                        }
                    ],
                },
                "comparisons": "control_vs_each_disease",
            }
        ),
        encoding="utf-8",
    )
    proj = load_project(p)
    labels = [x[0] for x in proj.get_resolved_groups()]
    assert labels == ["pool", "pca_I", "pca_II"]
    comps = proj.get_comparisons()
    assert len(comps) == 2
    assert {(c.control_group, c.disease_group) for c in comps} == {("pool", "pca_I"), ("pool", "pca_II")}
    ch = proj.cohort_hierarchy
    assert ch is not None
    assert ch["disease_families"][0]["leaves"] == ["pca_I", "pca_II"]


def test_group_rejects_stages_and_sample_paths():
    from methyl_utils.pipeline_config import GroupConfig

    with pytest.raises(ValueError, match="sample_paths or stages"):
        GroupConfig(
            label="x",
            sample_paths=["a.csv"],
            stages=[GroupConfig(label="s", sample_paths=["b.csv"])],
        )
