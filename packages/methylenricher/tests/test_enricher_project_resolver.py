"""Regression tests for comparison-aware enricher path resolution."""

import json
from pathlib import Path

import pytest

from methyl_enricher.project_resolver import (
    DEFAULT_METHYL_ENRICHER_HOME,
    resolve_enricher_paths,
    resolve_enricher_paths_per_cancer_group,
    resolve_methyl_enricher_home,
)


def _write_project(tmp_path: Path) -> Path:
    project_path = tmp_path / "project.json"
    out_base = tmp_path / "output"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "EnricherProject",
                "output_base": str(out_base),
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


def test_resolve_enricher_paths_defaults_to_project_roots(tmp_path):
    project_path = _write_project(tmp_path)
    out_base = tmp_path / "output"

    paths = resolve_enricher_paths(project_path)

    assert paths.input_file == str(out_base / "EnricherProject/mapper/all-gene_name-combined.csv")
    assert paths.output_dir == str(out_base / "EnricherProject/enricher")


def test_resolve_enricher_paths_per_comparison_uses_canonical_layout(tmp_path):
    project_path = _write_project(tmp_path)
    out_base = tmp_path / "output"

    resolved = resolve_enricher_paths_per_cancer_group(project_path)

    assert len(resolved) == 1
    paths, label = resolved[0]
    assert label == "pca"
    assert (
        paths.input_file
        == str(out_base / "EnricherProject/mapper/healthy/pca/all-gene_name-combined.csv")
    )
    assert paths.output_dir == str(out_base / "EnricherProject/enricher/healthy/pca")


def test_resolve_enricher_paths_warns_on_legacy_alias_keys(tmp_path):
    project_path = _write_project(tmp_path)
    override_path = tmp_path / "override.json"
    override_path.write_text(
        json.dumps({"input": "/tmp/legacy.csv", "outdir": "/tmp/legacy-out"}),
        encoding="utf-8",
    )

    with pytest.warns(DeprecationWarning):
        paths = resolve_enricher_paths(project_path, step_override_path=override_path)

    assert paths.input_file == "/tmp/legacy.csv"
    assert paths.output_dir == "/tmp/legacy-out"


def test_resolve_methyl_enricher_home_defaults(tmp_path):
    project_path = _write_project(tmp_path)
    home = resolve_methyl_enricher_home(project_path)
    assert home == DEFAULT_METHYL_ENRICHER_HOME


def test_resolve_methyl_enricher_home_from_site_config(tmp_path, monkeypatch):
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "EnricherProject",
                "output_base": str(tmp_path / "output"),
                "groups": [{"label": "g1", "sample_paths": []}, {"label": "g2", "sample_paths": []}],
            }
        ),
        encoding="utf-8",
    )
    site = tmp_path / "methyl_site.json"
    site.write_text(
        json.dumps({"actionConfig": {"enricher": {"methyl_enricher_home": "/work/cache/custom_enricher"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site))
    home = resolve_methyl_enricher_home(project_path)
    assert home == "/work/cache/custom_enricher"
