"""Tests for fragmentomics readiness summarization."""

import json
from pathlib import Path

from methyl_validation.fragmentomics_context import (
    build_fragmentomics_report,
    summarize_alignment_qc_fragmentomics,
)


def test_summarize_alignment_qc_fragmentomics(tmp_path: Path):
    qc_dir = tmp_path / "alignment_qc"
    qc_dir.mkdir()
    payload = {
        "sample_id": "s1",
        "fragmentomics_metrics": {
            "profile": "cfdna",
            "median_insert_size": 167,
            "short_fragment_fraction": 0.1,
            "long_fragment_fraction": 0.02,
            "nucleosome_peak_bp": 167,
            "short_fragment_max_bp": 150,
            "nucleosome_peak_bp_min": 140,
            "nucleosome_peak_bp_max": 200,
        },
        "guardrails": {
            "details": {
                "fragmentomics": {
                    "median_insert_bp": {"pass": True},
                }
            }
        },
    }
    (qc_dir / "s1.json").write_text(json.dumps(payload), encoding="utf-8")
    summary = summarize_alignment_qc_fragmentomics(qc_dir)
    assert summary["n_samples_with_metrics"] == 1
    assert summary["cohort_median_insert_size"] == 167


def test_build_fragmentomics_report_cfdna_expected(tmp_path: Path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    production = {
        "regulatory": {"primary_analyte": "cfdna", "sample_type": "plasma"},
    }

    def _fake_resolve(production_project, action_key):
        if action_key == "fragmentomics":
            return {"enabled": True}
        if action_key == "mapper":
            return {"disease_term": "Prostate adenocarcinoma"}
        return {}

    monkeypatch.setattr(
        "methyl_validation.fragmentomics_context._resolve_action_from_project_dict",
        _fake_resolve,
    )
    report = build_fragmentomics_report(project_root=root, production_project=production)
    assert report["expected_for_analyte"] is True
    assert report["fragmentomics_step_enabled"] is True
