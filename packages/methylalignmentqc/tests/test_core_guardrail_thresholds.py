"""Core WGBS guardrail thresholds must come from config, not hardcoded numbers."""

from pathlib import Path

from methyl_alignment_qc.core.wgbs_parabricks_qc import _build_wgbs_guardrail_report
from methyl_alignment_qc.models.config import AlignmentQCConfig, CoreGuardrailsConfig

from test_writer_guardrails import _parabricks_json_fixture

CORE_QC_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "methyl_alignment_qc"
    / "core"
    / "wgbs_parabricks_qc.py"
)


def _payload_with_gc_dropout(value: float) -> dict:
    payload = _parabricks_json_fixture("S1")
    payload["gc_bias_summary"]["gc_dropout"] = value
    return payload


def test_gc_dropout_uses_default_acceptance_window() -> None:
    """Unset config keeps the published gate closed (5.102 still fails)."""
    report = _build_wgbs_guardrail_report(_payload_with_gc_dropout(5.102))
    assert report["details"]["gc_dropout"]["pass"] is False
    assert report["overall_pass"] is False


def test_gc_dropout_threshold_is_operator_configurable() -> None:
    """Widening the threshold via config passes the same sample."""
    report = _build_wgbs_guardrail_report(
        _payload_with_gc_dropout(5.102),
        core_guardrails=CoreGuardrailsConfig(max_gc_dropout=6.0),
    )
    detail = report["details"]["gc_dropout"]
    assert detail["pass"] is True
    assert detail["normal_range"] == "< 6.0"
    assert report["overall_pass"] is True


def test_core_thresholds_are_reported_from_config() -> None:
    cfg = CoreGuardrailsConfig(
        min_pf_percent=80.0,
        min_q30_percent=70.0,
        min_mean_quality=30.0,
        min_quality_post20=25.0,
        max_at_dropout=4.0,
        max_gc_dropout=6.0,
        median_insert_min_bp=100,
        median_insert_max_bp=400,
        max_deamination_qscore=40,
        min_oxog_qscore=10,
    )
    report = _build_wgbs_guardrail_report(_parabricks_json_fixture("S1"), core_guardrails=cfg)
    ranges = {k: v["normal_range"] for k, v in report["details"].items()}
    assert ranges["pf_percent"] == ">= 80.0"
    assert ranges["q30_percent"] == ">= 70.0"
    assert ranges["at_dropout"] == "< 4.0"
    assert ranges["median_insert_bp"] == "100-400"
    assert ranges["oxog_qscore"] == ">= 10"


def test_q30_argument_still_overrides_config() -> None:
    report = _build_wgbs_guardrail_report(
        _parabricks_json_fixture("S1"),
        q30_threshold=95.0,
        core_guardrails=CoreGuardrailsConfig(min_q30_percent=70.0),
    )
    assert report["details"]["q30_percent"]["pass"] is False


def test_alignment_qc_config_accepts_core_guardrails_dict() -> None:
    cfg = AlignmentQCConfig.model_validate(
        {
            "sample_paths": ["/tmp/s1"],
            "output_dir": "/tmp/out",
            "core_guardrails": {"max_gc_dropout": 6.0},
        }
    )
    assert cfg.core_guardrails is not None
    assert cfg.core_guardrails.max_gc_dropout == 6.0


def test_no_hardcoded_comparison_literals_in_guardrail_evaluation() -> None:
    """Guard against reintroducing inline thresholds in the guardrail evaluator."""
    source = CORE_QC_SOURCE.read_text(encoding="utf-8")
    body = source.split("# Compile results against operator-set thresholds", 1)[1]
    body = body.split("results = {", 1)[0]
    resolved_names = ("cfg.", "q30_threshold")
    for line in body.splitlines():
        if "=" not in line:
            continue
        assert any(name in line for name in resolved_names), (
            f"hardcoded threshold in guardrail line: {line}"
        )
