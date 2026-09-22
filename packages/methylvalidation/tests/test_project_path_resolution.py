from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from methyl_validation import generative_backend, model_bundle, tabular_backend


class _StubProject:
    def __init__(self, detection_dir: Path):
        self.project_name = "stubproj"
        self._det = detection_dir

    def get_comparisons(self):
        return [
            SimpleNamespace(
                control_group="healthy",
                disease_group="pca1",
                comparison_label="healthy_vs_pca1",
            )
        ]

    def get_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        assert control_group == "healthy"
        assert disease_group == "pca1"
        return str(self._det)

    def resolve_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        return self.get_detection_output_dir(control_group, disease_group)

    def get_derived_paths(self):
        return SimpleNamespace(detection_dir=str(self._det))

    def get_resolved_groups(self):
        return [("healthy", ["/tmp/S1"]), ("pca1", ["/tmp/S2"])]


def test_model_bundle_loads_project_from_project_dir(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.7],
            "weight": [0.8],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)

    project_json = tmp_path / "cfg" / "project.json"
    project_json.parent.mkdir(parents=True)
    project_json.write_text("{}", encoding="utf-8")

    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    def _fake_load_project(_path):
        assert Path.cwd() == project_json.parent
        return _StubProject(det)

    monkeypatch.setattr(model_bundle, "load_project", _fake_load_project)
    model_bundle.build_model_feature_bundle(project_json=project_json, output_dir=tmp_path / "bundle")
    assert Path.cwd() == other_cwd


def test_tabular_train_loads_project_from_project_dir(tmp_path: Path, monkeypatch):
    project_json = tmp_path / "cfg" / "project.json"
    project_json.parent.mkdir(parents=True)
    project_json.write_text("{}", encoding="utf-8")

    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    def _fake_load_project(_path):
        assert Path.cwd() == project_json.parent
        raise RuntimeError("stop-after-load")

    monkeypatch.setattr(tabular_backend, "load_project", _fake_load_project)
    with pytest.raises(RuntimeError, match="stop-after-load"):
        tabular_backend.train_tabular_model(
            project_json=project_json,
            bundle_h5=tmp_path / "bundle.h5",
            output_dir=tmp_path / "model",
        )
    assert Path.cwd() == other_cwd


def test_generative_train_loads_project_from_project_dir(tmp_path: Path, monkeypatch):
    project_json = tmp_path / "cfg" / "project.json"
    project_json.parent.mkdir(parents=True)
    project_json.write_text("{}", encoding="utf-8")

    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    def _fake_load_project(_path):
        assert Path.cwd() == project_json.parent
        raise RuntimeError("stop-after-load")

    monkeypatch.setattr(generative_backend, "load_project", _fake_load_project)
    with pytest.raises(RuntimeError, match="stop-after-load"):
        generative_backend.train_generative_model(
            project_json=project_json,
            bundle_h5=tmp_path / "bundle.h5",
            output_dir=tmp_path / "model",
        )
    assert Path.cwd() == other_cwd
