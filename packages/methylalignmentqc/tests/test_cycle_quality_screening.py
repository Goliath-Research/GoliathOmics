"""Unit tests for read-end-aware cycle quality screening."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_alignment_qc.core.cycle_quality_screening import (
    DISPOSITION_GUARDRAIL_ONLY,
    DISPOSITION_MULTI_REGION,
    DISPOSITION_REALIGN_TRIM,
    DISPOSITION_USE_CURRENT,
    _compute_trim_front2,
    apply_screening_recommendations,
    screen_cycle_quality,
)
from methyl_alignment_qc.models.config import CycleScreeningConfig


def _cycles_payload(cycles: list[int], quals: list[float]) -> dict:
    return {
        "mean_quality_by_cycle": {
            "cycle": cycles,
            "mean_quality": quals,
        }
    }


def _guardrails(*, overall_pass: bool, cycle_pass: bool = True) -> dict:
    return {
        "overall_pass": overall_pass,
        "details": {
            "mean_quality": {"pass": cycle_pass},
            "min_quality_post20": {"pass": cycle_pass},
            "pf_percent": {"pass": not overall_pass},
        },
    }


def test_screen_pass_when_overall_pass():
    payload = _cycles_payload(list(range(1, 303)), [35.0] * 302)
    screening = screen_cycle_quality(payload, _guardrails(overall_pass=True))
    assert screening["disposition"] == DISPOSITION_USE_CURRENT
    assert screening["trim_front2"] == 0


def test_screen_pass_when_overall_pass_despite_low_cycles():
    """Guardrail pass short-circuits; screening must not recommend realign."""
    cycles = list(range(1, 303))
    quals = [35.0] * 302
    for i in range(5):
        quals[151 + i] = 25.0
    payload = _cycles_payload(cycles, quals)
    screening = screen_cycle_quality(
        payload,
        _guardrails(overall_pass=True),
        CycleScreeningConfig(read_length=151),
    )
    assert screening["disposition"] == DISPOSITION_USE_CURRENT
    assert screening["quality_pattern"] == "READ2_START_LOW_QUALITY"
    assert screening["trim_front2"] == 0


def test_compute_trim_front2_requires_recovery():
    """No trim when R2 never recovers above threshold after the low-quality run."""
    cycle_quals = [(c, 25.0) for c in range(152, 170)]
    assert _compute_trim_front2(cycle_quals, 152, 30.0, recovery_cycles=10, max_trim=8) == 0

    recovering = [(152, 25.0), (153, 25.0), (154, 35.0), (155, 35.0)]
    assert _compute_trim_front2(recovering, 152, 30.0, recovery_cycles=10, max_trim=8) == 2


def test_screen_r2_no_recovery_avoids_realign_trim():
    cycles = list(range(1, 303))
    quals = [35.0] * 302
    for i in range(151, 302):
        quals[i] = 25.0
    payload = _cycles_payload(cycles, quals)
    cfg = CycleScreeningConfig(read_length=151, max_trim_bases=8, recovery_cycles=10)
    screening = screen_cycle_quality(payload, _guardrails(overall_pass=False), cfg)
    assert screening["trim_front2"] == 0
    assert screening["disposition"] != DISPOSITION_REALIGN_TRIM


def test_screen_r2_start_dip_only():
    cycles = list(range(1, 303))
    quals = [35.0] * 302
    for i in range(5):
        quals[151 + i] = 25.0
    payload = _cycles_payload(cycles, quals)
    cfg = CycleScreeningConfig(read_length=151)
    screening = screen_cycle_quality(payload, _guardrails(overall_pass=False), cfg)
    assert screening["disposition"] == DISPOSITION_REALIGN_TRIM
    assert screening["trim_front2"] >= 1
    guardrails = {"recommendation": "FAIL", "next_steps": "x"}
    apply_screening_recommendations(guardrails, screening)
    assert "trim_front2" in guardrails["recommendation"].lower() or "Trim" in guardrails["recommendation"]


def test_screen_multi_region():
    cycles = list(range(1, 303))
    quals = [35.0] * 302
    quals[50] = 20.0
    quals[51] = 20.0
    for i in range(5):
        quals[151 + i] = 25.0
    payload = _cycles_payload(cycles, quals)
    cfg = CycleScreeningConfig(read_length=151)
    screening = screen_cycle_quality(payload, _guardrails(overall_pass=False), cfg)
    assert screening["disposition"] == DISPOSITION_MULTI_REGION


def test_screen_guardrail_only():
    payload = _cycles_payload(list(range(1, 303)), [35.0] * 302)
    screening = screen_cycle_quality(
        payload,
        _guardrails(overall_pass=False, cycle_pass=True),
        CycleScreeningConfig(read_length=151),
    )
    assert screening["disposition"] == DISPOSITION_GUARDRAIL_ONLY


def test_fixture_003772_r2_trim(tmp_path: Path):
    fixture = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "003772_8C9_3"
        / "003772_8C9_3.json"
    )
    if not fixture.is_file():
        pytest.skip(f"fixture missing: {fixture}")
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    guardrails = {"overall_pass": False, "details": {"min_quality_post20": {"pass": False}}}
    screening = screen_cycle_quality(
        payload,
        guardrails,
        CycleScreeningConfig(read_length=151, r2_quality_threshold=31.0),
    )
    assert screening["trim_front2"] > 0


def test_writer_qc_history_merge_on_retry(tmp_path: Path):
    from methyl_alignment_qc.core.qc_write_context import QcWriteContext
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons

    dedup = """## METRICS CLASS\tpicard.sam.markduplicates.MarkDuplicatesMetrics
LIBRARY\tUNPAIRED_READS_EXAMINED\tREAD_PAIRS_EXAMINED\tSECONDARY_OR_SUPPLEMENTARY_RDS\tUNMAPPED_READS\tUNPAIRED_READ_DUPLICATES\tREAD_PAIR_DUPLICATES\tREAD_PAIR_OPTICAL_DUPLICATES\tPERCENT_DUPLICATION\tESTIMATED_LIBRARY_SIZE
lib1\t0\t100\t0\t0\t0\t8\t1\t0.08\t1000
## HISTOGRAM\tjava.lang.Double
BIN\tVALUE
1\t100
2\t20
""".replace("\\t", "\t")

    sample_dir = tmp_path / "sampleR"
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "sampleR.deduplicate_metrics.txt").write_text(dedup, encoding="utf-8")
    fixture = {
        "sample_id": "sampleR",
        "quality_yield": {
            "total_reads": 1000,
            "pf_reads": 950,
            "total_bases": 120000,
            "pf_bases": 100000,
            "q20_bases": 110000,
            "pf_q20_bases": 98000,
            "q30_bases": 95000,
            "pf_q30_bases": 90000,
            "q20_equivalent_yield": 200000,
            "pf_q20_equivalent_yield": 190000,
        },
        "mean_quality_by_cycle": {"cycle": [1, 2, 3, 4, 5], "mean_quality": [38.0, 37.5, 36.2, 35.5, 35.0]},
        "quality_score_distribution": {"Q": [20, 30], "COUNT_OF_Q": [100, 90]},
        "base_distribution_by_cycle": {
            "cycle": [1, 2, 3, 4, 5],
            "PCT_A": [25.0] * 5,
            "PCT_C": [25.0] * 5,
            "PCT_G": [25.0] * 5,
            "PCT_T": [24.9] * 5,
            "PCT_N": [0.1] * 5,
        },
        "gc_bias_summary": {"at_dropout": 1.2, "gc_dropout": 2.3},
        "gc_bias_details": {
            "GC": [0, 1],
            "WINDOWS": [10, 10],
            "READ_STARTS": [5, 5],
            "MEAN_BASE_QUALITY": [30.0, 30.0],
            "NORMALIZED_COVERAGE": [1.0, 1.0],
            "ERROR_BAR": [0.1, 0.1],
        },
        "insert_size_metrics": {
            "median_insert_size": 210,
            "mode_insert_size": 200,
            "mean_insert_size": 220.0,
            "standard_deviation": 10.0,
            "read_pairs": 100,
            "width_of_10_percent": 10,
            "width_of_20_percent": 20,
            "width_of_30_percent": 30,
            "width_of_40_percent": 40,
            "width_of_50_percent": 50,
            "width_of_60_percent": 60,
            "width_of_70_percent": 70,
            "width_of_80_percent": 80,
            "width_of_90_percent": 90,
            "width_of_95_percent": 95,
            "width_of_99_percent": 99,
        },
        "insert_size_histogram": {
            "insert_size": [100, 101],
            "pair_orientation": ["FR", "FR"],
            "All_Reads.fr_count": [10, 20],
            "VALUE": [1.0, 2.0],
            "all_sets": [1, 1],
            "optical_sets": [0, 0],
            "non_optical_sets": [1, 1],
        },
        "error_summaries": {"REF": ["A"], "ALT": ["T"], "COUNT": [10], "RATE": [0.1], "QSCORE": [20]},
        "pre_adapter_summaries": {
            "ARTIFACT_NAME": ["Deamination"],
            "TOTAL_QSCORE": [5],
            "WORST_CXT": ["ACA"],
            "WORST_CXT_QSCORE": [5],
        },
        "bait_bias_summaries": {
            "ARTIFACT_NAME": ["Cref"],
            "TOTAL_QSCORE": [40],
            "WORST_CXT": ["CCA"],
            "WORST_CXT_QSCORE": [35],
        },
        "conversion_log": {"program": "Parabricks", "version": "4.6.0", "start_time": "t", "end_time": "t", "total_time": "1m"},
    }
    (sample_dir / "sampleR.json").write_text(json.dumps(fixture), encoding="utf-8")
    out_dir = tmp_path / "out"

    process_samples_to_qc_jsons([str(sample_dir)], str(out_dir), validate_schema=True)
    qc_path = out_dir / "sampleR.json"
    assert qc_path.is_file()
    first = json.loads(qc_path.read_text(encoding="utf-8"))
    assert len(first.get("qc_history") or []) == 1

    ctx = QcWriteContext(
        attempt=2,
        attempt_reason="Read 2 start low quality; trim_front2=5 before realign",
        alignment_pass="post_trim_realign",
        remediation_trigger={"disposition": "REALIGN_READ2_TRIM", "trimFront2": 5},
    )
    process_samples_to_qc_jsons([str(sample_dir)], str(out_dir), validate_schema=True, write_context=ctx)
    second = json.loads(qc_path.read_text(encoding="utf-8"))
    history = second.get("qc_history") or []
    assert len(history) == 2
    assert history[0]["attempt"] == 1
    assert history[1]["attempt"] == 2
    assert "trim_front2=5" in history[1]["reason"]
