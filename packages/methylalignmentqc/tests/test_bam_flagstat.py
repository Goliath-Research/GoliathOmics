"""Tests for samtools flagstat parsing and guardrails."""

from __future__ import annotations

import pytest

from methyl_alignment_qc.core.bam_flagstat import (
    _build_flagstat_metrics,
    apply_flagstat_guardrails,
    parse_flagstat_text,
)
from methyl_alignment_qc.models.config import AlignmentGuardrailsConfig

FLAGSTAT_FIXTURE = """1000 + 0 in total (QC-passed reads + QC-failed reads)
50 + 0 secondary
10 + 0 supplementary
100 + 0 duplicates
950 + 0 mapped
1000 + 0 paired in sequencing
900 + 0 read1
900 + 0 read2
850 + 0 properly paired (N-BAIMPROPER)
50 + 0 with itself and mate mapped
0 + 0 singletons (itself mapped; mate unmapped)
0 + 0 with mate mapped to a different chr
0 + 0 with mate mapped to a different chr (mapQ>=5)
"""


def test_parse_flagstat_text() -> None:
    counts = parse_flagstat_text(FLAGSTAT_FIXTURE)
    assert counts["total_reads"] == 1000
    assert counts["properly_paired_reads"] == 850
    assert counts["supplementary_reads"] == 10
    # Must not let "with itself and mate mapped" overwrite mapped_reads.
    assert counts["mapped_reads"] == 950
    assert counts["mate_mapped_reads"] == 50


def test_build_flagstat_metrics_properly_paired_rate() -> None:
    """samtools counts properly paired per read, not per pair — no 2x multiplier."""
    counts = parse_flagstat_text(FLAGSTAT_FIXTURE)
    metrics = _build_flagstat_metrics(counts)
    assert metrics.properly_paired_rate == 0.85
    assert metrics.supplementary_rate == 0.01
    assert metrics.mapped_rate == 0.95
    assert metrics.model_dump().keys() == {
        "total_reads",
        "mapped_reads",
        "properly_paired_reads",
        "supplementary_reads",
        "secondary_reads",
        "duplicate_reads",
        "properly_paired_rate",
        "supplementary_rate",
        "mapped_rate",
    }


def test_build_flagstat_metrics_is_alignment_flagstat_model() -> None:
    from methyl_alignment_qc.models.sample_qc import AlignmentFlagstat

    counts = parse_flagstat_text(FLAGSTAT_FIXTURE)
    metrics = _build_flagstat_metrics(counts)
    assert isinstance(metrics, AlignmentFlagstat)


def test_apply_flagstat_guardrails_fail_low_pairing() -> None:
    counts = parse_flagstat_text(FLAGSTAT_FIXTURE)
    metrics = _build_flagstat_metrics(counts)
    report = {"overall_pass": True, "recommendation": "PASS", "details": {}}
    cfg = AlignmentGuardrailsConfig(
        enabled=True,
        flagstat_enabled=True,
        min_properly_paired_rate=0.90,
    )
    apply_flagstat_guardrails(report, metrics, cfg)
    assert report["overall_pass"] is False
    assert report["details"]["properly_paired_rate"]["pass"] is False
    assert report["details"]["properly_paired_rate"]["value"] == 0.85


def test_apply_flagstat_guardrails_pass() -> None:
    from methyl_alignment_qc.models.sample_qc import AlignmentFlagstat

    metrics = AlignmentFlagstat(
        properly_paired_rate=0.95,
        supplementary_rate=0.01,
    )
    report = {"overall_pass": True, "recommendation": "PASS", "details": {}}
    cfg = AlignmentGuardrailsConfig(
        enabled=True,
        flagstat_enabled=True,
        min_properly_paired_rate=0.90,
        max_supplementary_rate_flagstat=0.02,
    )
    apply_flagstat_guardrails(report, metrics, cfg)
    assert report["overall_pass"] is True
    assert report["details"]["properly_paired_rate"]["pass"] is True


def test_apply_flagstat_guardrails_fail_on_error() -> None:
    report = {"overall_pass": True, "recommendation": "PASS", "details": {}}
    cfg = AlignmentGuardrailsConfig(
        enabled=True,
        flagstat_enabled=True,
        min_properly_paired_rate=0.90,
    )
    apply_flagstat_guardrails(report, None, cfg, error="samtools not found")
    assert report["overall_pass"] is False


def test_flagstat_bam_preflight_error(tmp_path) -> None:
    from methyl_alignment_qc.core.bam_flagstat import flagstat_bam_preflight_error

    missing = tmp_path / "nope.bam"
    assert flagstat_bam_preflight_error(missing) == f"BAM not found for flagstat: {missing}"

    empty = tmp_path / "empty.bam"
    empty.write_bytes(b"")
    assert "empty (0 bytes)" in (flagstat_bam_preflight_error(empty) or "")

    tiny = tmp_path / "tiny.bam"
    tiny.write_bytes(b"\x1f\x8b" + b"\x00" * 10)
    err = flagstat_bam_preflight_error(tiny)
    assert err is not None
    assert "too small to be valid (12 bytes)" in err

    small_valid = tmp_path / "small.bam"
    small_valid.write_bytes(b"\x1f\x8b" + b"\x00" * 32)
    assert flagstat_bam_preflight_error(small_valid) is None


def test_run_flagstat_rejects_empty_bam(tmp_path) -> None:
    from methyl_alignment_qc.core.bam_flagstat import run_flagstat

    sample_dir = tmp_path / "sampleZ"
    sample_dir.mkdir()
    (sample_dir / "sampleZ.bam").write_bytes(b"")
    with pytest.raises(RuntimeError, match="BAM is empty"):
        run_flagstat(sample_dir, "sampleZ")


def test_validate_bam_accepts_bgzf_gzip_prefix(tmp_path) -> None:
    from methyl_alignment_qc.core.bam_flagstat import _validate_bam_for_flagstat

    bam = tmp_path / "realish.bam"
    # BGZF/gzip magic only; samtools would fail later on truncated data.
    bam.write_bytes(b"\x1f\x8b" + b"\x00" * 32)
    _validate_bam_for_flagstat(bam)


def test_validate_bam_rejects_non_gzip_prefix(tmp_path) -> None:
    from methyl_alignment_qc.core.bam_flagstat import _validate_bam_for_flagstat

    bam = tmp_path / "fake.bam"
    bam.write_bytes(b"BAM\x01" + b"\x00" * 32)
    with pytest.raises(RuntimeError, match="BGZF-compressed BAM"):
        _validate_bam_for_flagstat(bam)
