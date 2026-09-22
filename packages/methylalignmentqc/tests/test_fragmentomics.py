"""Tests for cfDNA fragmentomics metrics and guardrails."""

from methyl_alignment_qc.core.fragmentomics import (
    apply_fragmentomics_to_payload,
    compute_fragmentomics_metrics,
    resolve_fragmentomics_config,
)
from methyl_alignment_qc.models.config import FragmentomicsConfig


def _cfdna_histogram_payload() -> dict:
    return {
        "insert_size_metrics": {"median_insert_size": 167},
        "insert_size_histogram": {
            "insert_size": [100, 120, 167, 180, 350],
            "All_Reads.fr_count": [50, 80, 400, 200, 30],
        },
        "guardrails": {
            "sample_id": "s1",
            "overall_pass": True,
            "details": {
                "pf_percent": {"value": 95, "normal_range": "", "pass": True, "message": ""},
                "q30_percent": {"value": 90, "normal_range": "", "pass": True, "message": ""},
                "mean_quality": {"value": 35, "normal_range": "", "pass": True, "message": ""},
                "min_quality_post20": {"value": 30, "normal_range": "", "pass": True, "message": ""},
                "at_dropout": {"value": 1, "normal_range": "", "pass": True, "message": ""},
                "gc_dropout": {"value": 2, "normal_range": "", "pass": True, "message": ""},
                "median_insert_bp": {"value": 167, "normal_range": "", "pass": True, "message": ""},
                "deamination_qscore": {"value": 10, "normal_range": "", "pass": True, "message": ""},
                "oxog_qscore": {"value": 25, "normal_range": "", "pass": True, "message": ""},
            },
            "recommendation": "OK",
            "next_steps": "",
        },
    }


def test_compute_fragmentomics_metrics_cfdna():
    cfg = FragmentomicsConfig(enabled=True, profile="cfdna")
    metrics = compute_fragmentomics_metrics(_cfdna_histogram_payload(), cfg)
    assert metrics.profile == "cfdna"
    assert metrics.median_insert_size == 167
    assert metrics.nucleosome_peak_bp == 167
    assert 0.0 < metrics.short_fragment_fraction < 0.2
    assert metrics.long_fragment_fraction > 0


def test_apply_fragmentomics_adds_guardrails_and_fails_short_fraction():
    payload = _cfdna_histogram_payload()
    cfg = FragmentomicsConfig(
        enabled=True,
        profile="cfdna",
        max_short_fragment_fraction=0.01,
    )
    apply_fragmentomics_to_payload(payload, cfg)
    assert "fragmentomics_metrics" in payload
    assert payload["fragmentomics_metrics"]["nucleosome_peak_bp"] == 167
    frag = payload["guardrails"]["details"]["fragmentomics"]
    assert frag is not None
    assert payload["guardrails"]["overall_pass"] is False


def test_resolve_fragmentomics_from_primary_analyte():
    cfg = resolve_fragmentomics_config(
        {"auto_profile_from_analyte": True},
        project_regulatory={"primary_analyte": "cfdna"},
    )
    assert cfg is not None
    assert cfg.is_active()
    assert cfg.resolved_profile() == "cfdna"


def test_resolve_fragmentomics_off_when_disabled():
    assert resolve_fragmentomics_config({"fragmentomics": {"enabled": False}}) is None
