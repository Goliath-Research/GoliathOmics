"""REALIGN_TRIM from conversion/mapped-rate when cycle metrics are absent."""

from __future__ import annotations

from pathlib import Path

from methyl_alignment_qc.core.writer import _apply_screening_and_audit
from methyl_alignment_qc.models.config import CycleScreeningConfig


def test_remediate_without_cycles_emits_realign_trim(tmp_path: Path) -> None:
    payload = {
        "guardrails": {
            "overall_pass": False,
            "details": {
                "bisulfite_conversion": {
                    "conversion_rate_pct": {"pass": False, "value": 90.0, "normal_range": ">= 99", "message": "low"}
                },
            },
        },
        "bisulfite_conversion_metrics": {"conversion_rate_pct": 90.0},
    }
    cfg = CycleScreeningConfig(
        enabled=True,
        remediate_without_cycles=True,
        fallback_trim_front=5,
        fallback_trim_tail=0,
    )
    out = tmp_path / "S.json"
    _apply_screening_and_audit(
        payload,
        cycle_screening=cfg,
        optional_guardrails=None,
        write_ctx=None,
        output_path=out,
        sample_dir=tmp_path,
        sample_name="S",
    )
    screening = payload["guardrails"]["screening"]
    assert screening["disposition"] == "REALIGN_TRIM"
    assert screening["trim_front1"] == 5
    assert screening["trim_spec"] == {"read": 1, "end": "start", "bases": 5}
    # Must validate as ExportedSampleQCPayload TrimSpec (node 942 regression).
    from methyl_alignment_qc.models.sample_qc import QcScreeningReport

    QcScreeningReport.model_validate(screening)


def test_no_cycle_default_stays_use_current(tmp_path: Path) -> None:
    payload = {"guardrails": {"overall_pass": True, "details": {}}}
    cfg = CycleScreeningConfig(enabled=True)
    _apply_screening_and_audit(
        payload,
        cycle_screening=cfg,
        optional_guardrails=None,
        write_ctx=None,
        output_path=tmp_path / "S.json",
        sample_dir=tmp_path,
        sample_name="S",
    )
    assert payload["guardrails"]["screening"]["disposition"] == "USE_CURRENT_ALIGNMENT"
