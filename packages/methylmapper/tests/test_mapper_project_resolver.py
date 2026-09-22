"""Regression tests for comparison-aware mapper path resolution."""

import json
import os
from pathlib import Path

from methyl_mapper.project_resolver import (
    DMP_CSV_PATTERN_BIOLOGICAL,
    DMP_CSV_PATTERN_DISCOVERY,
    resolve_mapper_paths,
    resolve_mapper_paths_per_cancer_group,
)


def _write_project(tmp_path: Path, *, csv_pattern: str = "dmps-*.csv") -> Path:
    profile_path = tmp_path / "mapper.profile.json"
    profile_path.write_text(
        json.dumps({"pipelineProfile": "mapper_test", "actionConfig": {"mapper": {"csv_pattern": csv_pattern}}}),
        encoding="utf-8",
    )
    os.environ["METHYL_PROFILE"] = str(profile_path)
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "MapperProject",
                "output_base": str(tmp_path / "output"),
                "controls": {
                    "label": "controls",
                    "groups": [{"label": "healthy", "sample_paths": ["c1"]}],
                },
                "diseases": {
                    "label": "diseases",
                    "groups": [{"label": "pca", "sample_paths": ["d1"]}],
                },
                "comparisons": [{"control_group": "healthy", "disease_group": "pca"}],
            }
        ),
        encoding="utf-8",
    )
    return project_path


def test_resolve_mapper_paths_uses_comparison_wildcards(tmp_path):
    project_path = _write_project(tmp_path)
    out_base = tmp_path / "output"

    paths = resolve_mapper_paths(project_path)

    assert paths.csv_pattern == str(out_base / "MapperProject/detections/*/*/dmps-*.csv")
    assert paths.output_dir == str(out_base / "MapperProject/mapper")


def test_resolve_mapper_paths_per_comparison_uses_canonical_layout(tmp_path):
    project_path = _write_project(tmp_path)
    out_base = tmp_path / "output"

    resolved = resolve_mapper_paths_per_cancer_group(
        project_path,
        csv_filename_pattern=DMP_CSV_PATTERN_BIOLOGICAL,
    )

    assert len(resolved) == 1
    paths, label = resolved[0]
    assert label == "pca"
    assert (
        paths.csv_pattern
        == str(out_base / "MapperProject/detections/healthy/pca/dmps-*.csv")
    )
    assert paths.output_dir == str(out_base / "MapperProject/mapper/healthy/pca")


def test_resolve_mapper_paths_per_comparison_honors_step_override_output_dir(tmp_path):
    project_path = _write_project(tmp_path)
    out_base = tmp_path / "output"
    override = tmp_path / "mapper_classifier_override.json"
    override_dir = str(out_base / "MapperProject/mapper_classifier/healthy/pca")
    override.write_text(
        json.dumps(
            {
                "csv_pattern": "dmps-*-classifier.csv",
                "output_dir": override_dir,
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_mapper_paths_per_cancer_group(project_path, override)

    assert len(resolved) == 1
    paths, label = resolved[0]
    assert label == "pca"
    assert paths.csv_pattern.endswith("/detections/healthy/pca/dmps-*-classifier.csv")
    assert paths.output_dir == override_dir


def test_resolve_mapper_paths_per_comparison_output_dir_base_appends_each_comparison(tmp_path):
    out_base = tmp_path / "output"
    project_path = _write_project(tmp_path)
    project_path.write_text(
        json.dumps(
            {
                "project_name": "MapperProject",
                "output_base": str(out_base),
                "controls": {
                    "label": "controls",
                    "groups": [{"label": "healthy", "sample_paths": ["c1"]}],
                },
                "diseases": {
                    "label": "diseases",
                    "groups": [
                        {"label": "pca_low", "sample_paths": ["d1"]},
                        {"label": "pca_high", "sample_paths": ["d2"]},
                    ],
                },
                "comparisons": [
                    {"control_group": "healthy", "disease_group": "pca_low"},
                    {"control_group": "healthy", "disease_group": "pca_high"},
                ],
            }
        ),
        encoding="utf-8",
    )
    override = tmp_path / "mapper_classifier_base.json"
    override.write_text(
        json.dumps(
            {
                "csv_pattern": "dmps-*-classifier.csv",
                "output_dir": str(out_base / "MapperProject/mapper_classifier"),
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_mapper_paths_per_cancer_group(project_path, override)

    assert len(resolved) == 2
    by_label = {label: paths for paths, label in resolved}
    assert by_label["pca_low"].output_dir == str(
        out_base / "MapperProject/mapper_classifier/healthy/pca_low"
    )
    assert by_label["pca_high"].output_dir == str(
        out_base / "MapperProject/mapper_classifier/healthy/pca_high"
    )
    assert by_label["pca_low"].output_dir != by_label["pca_high"].output_dir


def test_resolve_mapper_paths_per_comparison_falls_back_to_existing_case_variant(tmp_path):
    out_base = tmp_path / "out"
    _write_project(tmp_path, csv_pattern="dmps-*-discovery.csv")
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "MapperCaseProject",
                "output_base": str(out_base),
                "controls": {
                    "label": "controls",
                    "groups": [{"label": "all", "sample_paths": ["c1"]}],
                },
                "diseases": {
                    "label": "diseases",
                    "groups": [{"label": "PCa_PCa1", "sample_paths": ["d1"]}],
                },
                "comparisons": [{"control_group": "all", "disease_group": "PCa_PCa1"}],
            }
        ),
        encoding="utf-8",
    )
    legacy_dir = out_base / "MapperCaseProject" / "detections" / "all" / "pca_pca1"
    legacy_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolve_mapper_paths_per_cancer_group(project_path)

    assert len(resolved) == 1
    paths, label = resolved[0]
    assert label == "PCa_PCa1"
    assert paths.csv_pattern.endswith("/detections/all/pca_pca1/dmps-*-discovery.csv")
