"""Tests for bisulfite conversion sidecar and guardrails."""

import json
from pathlib import Path

from methyl_alignment_qc.core.bisulfite_conversion import (
    apply_bisulfite_conversion_to_payload,
    resolve_bisulfite_metrics,
)
from methyl_alignment_qc.models.config import BisulfiteConversionConfig


def _minimal_payload() -> dict:
    return {
        "pre_adapter_summaries": {
            "ARTIFACT_NAME": ["Deamination"],
            "TOTAL_QSCORE": [15],
            "WORST_CXT": ["ACA"],
            "WORST_CXT_QSCORE": [15],
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
                "deamination_qscore": {"value": 15, "normal_range": "", "pass": True, "message": ""},
                "oxog_qscore": {"value": 25, "normal_range": "", "pass": True, "message": ""},
            },
            "recommendation": "OK",
            "next_steps": "Run extractor.",
        },
    }


def test_sidecar_conversion_guardrails_pass(tmp_path: Path):
    sample_dir = tmp_path / "sampleA"
    sample_dir.mkdir()
    (sample_dir / "bisulfite_conversion.json").write_text(
        json.dumps(
            {
                "conversion_rate_pct": 99.5,
                "non_cpg_methylation_pct": 0.8,
                "source": "lambda_spikein",
            }
        ),
        encoding="utf-8",
    )
    cfg = BisulfiteConversionConfig(enabled=True, source="sidecar")
    metrics = resolve_bisulfite_metrics(_minimal_payload(), sample_dir, cfg)
    assert metrics.conversion_rate_pct == 99.5
    assert metrics.non_cpg_methylation_pct == 0.8

    payload = _minimal_payload()
    apply_bisulfite_conversion_to_payload(payload, sample_dir, cfg)
    assert payload["bisulfite_conversion_metrics"]["conversion_rate_pct"] == 99.5
    conv = payload["guardrails"]["details"]["bisulfite_conversion"]["conversion_rate_pct"]
    passed = conv.model_dump(by_alias=True)["pass"] if hasattr(conv, "model_dump") else conv["pass"]
    assert passed is True


def test_low_conversion_fails_guardrail(tmp_path: Path):
    sample_dir = tmp_path / "sampleB"
    sample_dir.mkdir()
    (sample_dir / "bisulfite_conversion.json").write_text(
        json.dumps({"conversion_rate_pct": 97.0}),
        encoding="utf-8",
    )
    payload = _minimal_payload()
    cfg = BisulfiteConversionConfig(enabled=True, min_conversion_rate_pct=99.0)
    apply_bisulfite_conversion_to_payload(payload, sample_dir, cfg)
    assert payload["guardrails"]["overall_pass"] is False


def test_auto_proxy_does_not_alias_deamination(tmp_path: Path):
    sample_dir = tmp_path / "sampleC"
    sample_dir.mkdir()
    payload = _minimal_payload()
    cfg = BisulfiteConversionConfig(enabled=True, source="auto")
    apply_bisulfite_conversion_to_payload(payload, sample_dir, cfg)
    metrics = payload["bisulfite_conversion_metrics"]
    assert "deamination_qscore" not in metrics
    assert metrics["measurement_source"] == "deamination_proxy"
    details = payload["guardrails"]["details"]
    assert details["deamination_qscore"]["value"] == 15
    assert "deamination_qscore" not in (details.get("bisulfite_conversion") or {})
