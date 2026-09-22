"""Test project_resolver returns expected output_dir and sample_paths from project config."""

import json
from pathlib import Path

import pytest


def test_resolve_alignment_qc_config_output_dir_and_sample_paths(tmp_path: Path):
    """resolve_alignment_qc_config returns output_dir and non-empty sample_paths from minimal project."""
    from methyl_alignment_qc.project_resolver import resolve_alignment_qc_config

    out_base = tmp_path / "output"
    project = {
        "project_name": "TestProject",
        "output_base": str(out_base),
        "group1": {"label": "healthy", "sample_paths": ["/samples/h1", "/samples/h2"]},
        "group2": {"label": "disease", "sample_paths": ["/samples/d1"]},
    }
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    config = resolve_alignment_qc_config(str(project_path))
    assert config.output_dir == str(out_base / "TestProject" / "alignment_qc")
    assert len(config.sample_paths) == 3
    assert "/samples/h1" in config.sample_paths
    assert "/samples/h2" in config.sample_paths
    assert "/samples/d1" in config.sample_paths
    assert config.validate_schema is True


def _write_site(tmp_path: Path, action_key: str, cfg: dict) -> str:
    """Write a minimal site manifest carrying one action's config (replaces step_config)."""
    site = tmp_path / "methyl_site.json"
    site.write_text(json.dumps({"actionConfig": {action_key: cfg}}), encoding="utf-8")
    return str(site)


def test_resolve_alignment_qc_config_site_validate_schema(tmp_path, monkeypatch):
    """alignment_qc.validate_schema from site actionConfig is applied (config-not-code)."""
    from methyl_alignment_qc.project_resolver import resolve_alignment_qc_config

    out_base = tmp_path / "out"
    project = {
        "project_name": "Test",
        "output_base": str(out_base),
        "group1": {"label": "g1", "sample_paths": ["/a"]},
        "group2": {"label": "g2", "sample_paths": ["/b"]},
    }
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    monkeypatch.setenv("METHYL_SITE_CONFIG", _write_site(tmp_path, "alignment_qc", {"validate_schema": False}))
    config = resolve_alignment_qc_config(str(project_path))
    assert config.validate_schema is False
    assert config.output_dir == str(out_base / "Test" / "alignment_qc")


def test_resolve_alignment_qc_config_supports_control_and_label_filters(tmp_path, monkeypatch):
    """Canonical control/disease projects can filter QC inputs by side or group label."""
    from methyl_alignment_qc.project_resolver import resolve_alignment_qc_config

    out_base = tmp_path / "out"
    project = {
        "project_name": "CanonicalQC",
        "output_base": str(out_base),
        "controls": {
            "label": "controls",
            "groups": [{"label": "healthy", "sample_paths": ["/samples/h1", "/samples/shared"]}],
        },
        "diseases": {
            "label": "diseases",
            "groups": [
                {"label": "pca", "sample_paths": ["/samples/d1", "/samples/shared"]},
                {"label": "crc", "sample_paths": ["/samples/d2"]},
            ],
        },
        "comparisons": [
            {"control_group": "healthy", "disease_group": "pca"},
            {"control_group": "healthy", "disease_group": "crc"},
        ],
    }
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    monkeypatch.setenv("METHYL_SITE_CONFIG", _write_site(tmp_path, "alignment_qc", {"groups": ["control", "pca"]}))
    config = resolve_alignment_qc_config(str(project_path))
    assert config.output_dir == str(out_base / "CanonicalQC" / "alignment_qc")
    assert config.sample_paths == ["/samples/h1", "/samples/shared", "/samples/d1"]
