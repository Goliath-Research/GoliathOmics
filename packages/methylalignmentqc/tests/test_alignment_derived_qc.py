"""Tests for alignment-derived QC metrics and guardrails."""

from __future__ import annotations

from methyl_alignment_qc.core.alignment_derived_qc import (
    apply_alignment_derived_guardrails,
    compute_alignment_stats,
)
from methyl_alignment_qc.models.config import AlignmentGuardrailsConfig


def _payload_with_dedup(
    *,
    read_pairs: int = 100,
    unpaired: int = 0,
    unmapped: int = 1,
    secondary: int = 2,
) -> dict:
    return {
        "duplication_metrics": [
            {
                "LIBRARY": "lib1",
                "UNPAIRED_READS_EXAMINED": unpaired,
                "READ_PAIRS_EXAMINED": read_pairs,
                "SECONDARY_OR_SUPPLEMENTARY_RDS": secondary,
                "UNMAPPED_READS": unmapped,
                "UNPAIRED_READ_DUPLICATES": 0,
                "READ_PAIR_DUPLICATES": 0,
                "READ_PAIR_OPTICAL_DUPLICATES": 0,
                "PERCENT_DUPLICATION": 0.0,
                "ESTIMATED_LIBRARY_SIZE": 1000,
            }
        ],
        "gc_bias_details": {
            "WINDOWS": [10, 10, 10, 10],
            "NORMALIZED_COVERAGE": [0.8, 1.0, 1.2, 0.9],
        },
    }


def test_compute_alignment_stats_mapping_rate() -> None:
    payload = _payload_with_dedup(read_pairs=100, unmapped=2, secondary=4)
    stats = compute_alignment_stats(payload)
    assert stats is not None
    assert stats["reads_examined"] == 200
    assert stats["mapping_rate"] == round(1 - 2 / 200, 6)
    assert stats["secondary_supplementary_rate"] == round(4 / 200, 6)
    assert stats["gc_coverage_uniformity"] == round(0.8 / 0.95, 6)


def test_apply_alignment_guardrails_fail_low_mapping() -> None:
    payload = _payload_with_dedup(read_pairs=100, unmapped=50)
    report = {
        "overall_pass": True,
        "recommendation": "PASS: ok",
        "details": {},
    }
    cfg = AlignmentGuardrailsConfig(enabled=True, min_mapping_rate=0.98)
    apply_alignment_derived_guardrails(report, payload, cfg)
    assert report["overall_pass"] is False
    assert report["details"]["mapping_rate"]["pass"] is False


def test_apply_alignment_guardrails_disabled_skips() -> None:
    payload = _payload_with_dedup(read_pairs=100, unmapped=50)
    report = {"overall_pass": True, "recommendation": "PASS", "details": {}}
    apply_alignment_derived_guardrails(report, payload, AlignmentGuardrailsConfig(enabled=False))
    assert "mapping_rate" not in report["details"]
