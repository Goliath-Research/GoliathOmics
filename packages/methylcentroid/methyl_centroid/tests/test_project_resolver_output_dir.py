"""Ensure --output-dir and chr/context filters apply in project mode."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_centroid.project_resolver import resolve_centroid_batch_config


def _write_project(path: Path) -> None:
    root = path.parent
    control_csv = root / "control.csv"
    disease_csv = root / "disease.csv"
    sample = root / "samples" / "S1"
    sample.mkdir(parents=True)
    disease = root / "samples" / "D1"
    disease.mkdir(parents=True)
    (sample / "1-CG.h5").write_bytes(b"")
    (disease / "1-CG.h5").write_bytes(b"")
    control_csv.write_text(f"{sample}\n", encoding="utf-8")
    disease_csv.write_text(f"{root / 'samples' / 'D1'}\n", encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "project_name": "demo",
                "output_base": str(root),
                "samples_base_path": str(root / "samples"),
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": [str(control_csv)]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [{"label": "PCa", "sample_paths": [str(disease_csv)]}],
                },
                "comparisons": [{"control_group": "all", "disease_group": "PCa"}],
                "chromosomes": ["1", "2"],
                "contexts": ["CG"],
            }
        ),
        encoding="utf-8",
    )


def test_resolve_centroid_batch_config_honors_output_dir_override(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    _write_project(project)
    override = tmp_path / "seed" / "centroids" / "controls" / "healthy" / "all"
    batch = resolve_centroid_batch_config(
        project,
        "all",
        output_dir_override=override,
        chromosome="1",
        context="CG",
    )
    assert batch.base_config.output_dir == str(override.resolve())
    assert batch.chromosomes == ["1"]
    assert batch.contexts == ["CG"]


def test_resolve_centroid_batch_config_uses_project_paths_without_override(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    _write_project(project)
    batch = resolve_centroid_batch_config(project, "all")
    assert batch.base_config.output_dir.endswith("/demo/centroids/controls/healthy/all")
    assert batch.chromosomes == ["1", "2"]
