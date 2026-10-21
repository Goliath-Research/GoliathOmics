"""Baked extraction QC windows win over site defaults."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_extraction_qc.project_resolver import (
    extraction_qc_window,
    resolve_extraction_qc_config,
)

_RELAXED = {
    "expected_chromosomes": ["1", "21"],
    "guardrails": {
        "min_cpg_weighted_mean_coverage": 0,
        "max_chh_methylation_level": 1,
        "max_chg_methylation_level": 1,
        "min_autosomal_coverage_uniformity_ratio": 0,
        "max_discard_fraction": 1,
    },
}


def _project(tmp_path: Path) -> Path:
    project = {
        "project_name": "Canary",
        "output_base": str(tmp_path / "out"),
        "chromosomes": ["1", "21"],
        "group1": {"label": "g1", "sample_paths": ["/samples/a"]},
        "group2": {"label": "g2", "sample_paths": ["/samples/b"]},
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    return path


def _site(tmp_path: Path) -> Path:
    site = tmp_path / "site.json"
    site.write_text(
        json.dumps(
            {
                "actionConfig": {
                    "extraction_qc": {
                        "guardrails": {
                            "min_cpg_weighted_mean_coverage": 10.0,
                            "max_chh_methylation_level": 0.02,
                            "max_chg_methylation_level": 0.02,
                            "min_autosomal_coverage_uniformity_ratio": 0.5,
                            "max_discard_fraction": 0.9,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return site


def test_site_default_min_coverage_when_no_baked_window(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(_site(tmp_path)))
    config = resolve_extraction_qc_config(str(_project(tmp_path)), sample_paths=["/samples/S1"])
    assert config.guardrails.min_cpg_weighted_mean_coverage == 10.0
    assert config.expected_chromosomes == ["1", "21"]


def test_baked_resolved_config_wins_over_site(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(_site(tmp_path)))
    config = resolve_extraction_qc_config(
        str(_project(tmp_path)),
        sample_paths=["/samples/S1"],
        resolved_config=_RELAXED,
    )
    assert config.guardrails.min_cpg_weighted_mean_coverage == 0
    assert config.guardrails.max_chh_methylation_level == 1
    assert config.guardrails.max_discard_fraction == 1
    assert config.expected_chromosomes == ["1", "21"]
    assert config.sample_paths == ["/samples/S1"]


def test_sample_context_overlays_baked_window(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("METHYL_SITE_CONFIG", raising=False)
    pinned = resolve_extraction_qc_config(
        str(_project(tmp_path)),
        sample_paths=["/samples/S1"],
        resolved_config=_RELAXED,
        sample_context={"guardrails": {"min_cpg_weighted_mean_coverage": 20}},
    )
    assert pinned.guardrails.min_cpg_weighted_mean_coverage == 20
    assert pinned.guardrails.max_discard_fraction == 1

    window = extraction_qc_window(
        resolved_config=_RELAXED,
        sample_context={
            "actionConfig": {
                "extraction_qc": {"guardrails": {"min_cpg_weighted_mean_coverage": 4}}
            }
        },
    )
    assert window is not None
    assert window.guardrails.min_cpg_weighted_mean_coverage == 4
    assert window.expected_chromosomes == ["1", "21"]


def test_window_without_project_ignores_site(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(_site(tmp_path)))
    window = extraction_qc_window(resolved_config=_RELAXED)
    assert window is not None
    assert window.guardrails.min_cpg_weighted_mean_coverage == 0
    assert window.guardrails.min_autosomal_coverage_uniformity_ratio == 0
