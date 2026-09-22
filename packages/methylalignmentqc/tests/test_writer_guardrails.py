import json
from pathlib import Path

import pytest


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _dedup_metrics_fixture() -> str:
    return """## METRICS CLASS\tpicard.sam.markduplicates.MarkDuplicatesMetrics
LIBRARY\tUNPAIRED_READS_EXAMINED\tREAD_PAIRS_EXAMINED\tSECONDARY_OR_SUPPLEMENTARY_RDS\tUNMAPPED_READS\tUNPAIRED_READ_DUPLICATES\tREAD_PAIR_DUPLICATES\tREAD_PAIR_OPTICAL_DUPLICATES\tPERCENT_DUPLICATION\tESTIMATED_LIBRARY_SIZE
lib1\t0\t100\t0\t0\t0\t8\t1\t0.08\t1000
## HISTOGRAM\tjava.lang.Double
BIN\tVALUE
1\t100
2\t20
""".replace("\\t", "\t")


def _parabricks_json_fixture(sample_name: str) -> dict:
    return {
        "sample_id": sample_name,
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
            "PCT_A": [25.0, 25.0, 25.0, 25.0, 25.0],
            "PCT_C": [25.0, 25.0, 25.0, 25.0, 25.0],
            "PCT_G": [25.0, 25.0, 25.0, 25.0, 25.0],
            "PCT_T": [24.9, 24.9, 24.9, 24.9, 24.9],
            "PCT_N": [0.1, 0.1, 0.1, 0.1, 0.1],
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
        "error_summaries": {
            "REF": ["A", "C"],
            "ALT": ["T", "G"],
            "COUNT": [10, 20],
            "RATE": [0.1, 0.2],
            "QSCORE": [20, 30],
        },
        "pre_adapter_summaries": {
            "ARTIFACT_NAME": ["Deamination", "OxoG"],
            "TOTAL_QSCORE": [5, 25],
            "WORST_CXT": ["ACA", "TGC"],
            "WORST_CXT_QSCORE": [5, 25],
        },
        "bait_bias_summaries": {
            "ARTIFACT_NAME": ["Cref", "Gref"],
            "TOTAL_QSCORE": [40, 40],
            "WORST_CXT": ["CCA", "GGC"],
            "WORST_CXT_QSCORE": [35, 35],
        },
        "conversion_log": {
            "program": "Parabricks",
            "version": "4.6.0-1",
            "start_time": "now",
            "end_time": "later",
            "total_time": "1 minute",
        },
    }


def test_process_samples_to_qc_jsons_includes_guardrails_when_parabricks_json_present(tmp_path: Path):
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons

    sample_dir = tmp_path / "sampleA"
    sample_dir.mkdir(parents=True, exist_ok=True)

    _write_text(sample_dir / "sampleA.deduplicate_metrics.txt", _dedup_metrics_fixture())
    (sample_dir / "sampleA.json").write_text(json.dumps(_parabricks_json_fixture("sampleA")), encoding="utf-8")

    output_dir = tmp_path / "out"
    process_samples_to_qc_jsons([str(sample_dir)], str(output_dir), validate_schema=True)

    output_file = output_dir / "sampleA.json"
    assert output_file.exists()

    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload.get("metadata", {}).get("schema_version") == "2.1.0"
    assert payload["sample_id"] == "sampleA"
    assert "quality_yield" in payload
    assert "mean_quality_by_cycle" not in payload
    assert "duplication_histogram" not in payload
    assert "insert_size_histogram" not in payload
    assert "pre_adapter_summaries" not in payload
    assert "guardrails" in payload
    assert payload["guardrails"]["sample_id"] == "sampleA"
    assert "details" in payload["guardrails"]
    assert "pf_percent" in payload["guardrails"]["details"]
    assert "message" in payload["guardrails"]["details"]["pf_percent"]


def test_process_samples_to_qc_jsons_fails_when_parabricks_json_missing(tmp_path: Path):
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons

    sample_dir = tmp_path / "sampleB"
    sample_dir.mkdir(parents=True, exist_ok=True)
    _write_text(sample_dir / "sampleB.deduplicate_metrics.txt", _dedup_metrics_fixture())

    output_dir = tmp_path / "out"
    with pytest.raises(RuntimeError, match="Missing Parabricks metrics"):
        process_samples_to_qc_jsons([str(sample_dir)], str(output_dir), validate_schema=True)


def test_process_samples_to_qc_jsons_uses_qc_metrics_tar_when_json_is_guardrails_stub(tmp_path: Path):
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons

    sample_dir = tmp_path / "sampleD"
    sample_dir.mkdir(parents=True, exist_ok=True)
    _write_text(sample_dir / "sampleD.deduplicate_metrics.txt", _dedup_metrics_fixture())
    (sample_dir / "sampleD.json").write_text(
        json.dumps({"guardrails": {"overall_pass": True}}),
        encoding="utf-8",
    )

    output_dir = tmp_path / "out"
    with pytest.raises(RuntimeError, match="Missing Parabricks metrics"):
        process_samples_to_qc_jsons([str(sample_dir)], str(output_dir), validate_schema=True)


def test_build_sample_qc_v2_dict_falls_back_to_qc_metrics_tar(tmp_path: Path, monkeypatch) -> None:
    from methyl_alignment_qc.core.writer import build_sample_qc_v2_dict

    sample_dir = tmp_path / "sampleE"
    sample_dir.mkdir(parents=True, exist_ok=True)
    _write_text(sample_dir / "sampleE.deduplicate_metrics.txt", _dedup_metrics_fixture())
    (sample_dir / "sampleE.json").write_text(
        json.dumps({"guardrails": {"overall_pass": True}}),
        encoding="utf-8",
    )

    fixture = _parabricks_json_fixture("sampleE")

    def _fake_tar_build(sd: Path, name: str):
        assert name == "sampleE"
        return fixture

    monkeypatch.setattr(
        "methyl_alignment_qc.core.writer._build_parabricks_payload_from_qc_tar",
        _fake_tar_build,
    )
    monkeypatch.setattr(
        "methyl_alignment_qc.core.writer._find_qc_metrics_tar",
        lambda sd, name: sd / f"{name}.qc-metrics.tar",
    )

    payload = build_sample_qc_v2_dict(sample_dir, cycle_screening=None)
    assert payload["sample_id"] == "sampleE"
    assert "quality_yield" in payload


def test_process_samples_to_qc_jsons_uses_canonical_sample_json_name_only(tmp_path: Path):
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons

    sample_dir = tmp_path / "sampleC"
    sample_dir.mkdir(parents=True, exist_ok=True)
    _write_text(sample_dir / "sampleC.deduplicate_metrics.txt", _dedup_metrics_fixture())
    # Present but non-canonical JSON name; should not be auto-used.
    (sample_dir / "other_metrics.json").write_text(
        json.dumps(_parabricks_json_fixture("sampleC")),
        encoding="utf-8",
    )

    output_dir = tmp_path / "out"
    with pytest.raises(RuntimeError, match="Missing Parabricks metrics"):
        process_samples_to_qc_jsons([str(sample_dir)], str(output_dir), validate_schema=True)


def test_resolve_sample_artifact_id_mode_subdir(tmp_path: Path):
    from methyl_alignment_qc.core.parser import resolve_sample_artifact_id

    mode_dir = tmp_path / "DPLST-051425-111148" / "linear"
    mode_dir.mkdir(parents=True)
    (mode_dir / "DPLST-051425-111148.deduplicate_metrics.txt").write_text("x", encoding="utf-8")
    assert resolve_sample_artifact_id(mode_dir) == "DPLST-051425-111148"
    assert resolve_sample_artifact_id(mode_dir, "DPLST-051425-111148") == "DPLST-051425-111148"
    # Explicit wins even if dirname looks like a sample id
    flat = tmp_path / "sampleA"
    flat.mkdir()
    assert resolve_sample_artifact_id(flat, "sampleA") == "sampleA"
    assert resolve_sample_artifact_id(flat) == "sampleA"


def test_process_samples_to_qc_jsons_align_arm_uses_sample_id_not_dirname(
    tmp_path: Path, monkeypatch
) -> None:
    """Production arm leaf .../<sampleId>/align.linear.parabricks is not sampleId."""
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons
    from methyl_alignment_qc.models.config import AlignmentGuardrailsConfig

    sample_id = "DPLST-051425-111148"
    arm_dir = tmp_path / sample_id / "align.linear.parabricks"
    arm_dir.mkdir(parents=True)
    _write_text(arm_dir / f"{sample_id}.deduplicate_metrics.txt", _dedup_metrics_fixture())
    (arm_dir / f"{sample_id}.json").write_text(
        json.dumps(_parabricks_json_fixture(sample_id)), encoding="utf-8"
    )
    (arm_dir / f"{sample_id}.bam").write_bytes(b"\x1f\x8b" + b"\x00" * 64)

    def _fake_flagstat(sample_dir, sid, force=False):
        from methyl_alignment_qc.models.sample_qc import AlignmentFlagstat

        assert sid == sample_id
        return AlignmentFlagstat(
            total_reads=100,
            mapped_reads=95,
            properly_paired_reads=92,
            supplementary_reads=0,
            secondary_reads=0,
            duplicate_reads=0,
            mapped_rate=0.95,
            properly_paired_rate=0.92,
            supplementary_rate=0.0,
        )

    monkeypatch.setattr("methyl_alignment_qc.core.writer.run_flagstat", _fake_flagstat)

    output_dir = tmp_path / "out"
    process_samples_to_qc_jsons(
        [str(arm_dir)],
        str(output_dir),
        validate_schema=True,
        alignment_mode="linear",
        alignment_guardrails=AlignmentGuardrailsConfig(
            enabled=True,
            flagstat_enabled=True,
            min_properly_paired_rate=0.90,
        ),
    )

    out = output_dir / f"{sample_id}.json"
    assert out.is_file()
    assert not (output_dir / "align.linear.parabricks.json").exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["sample_id"] == sample_id
    assert payload["guardrails"]["metrics_family"] == "parabricks"


def test_process_samples_to_qc_jsons_mode_subdir_uses_sample_id_not_dirname(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: compare experiment uses .../<sampleId>/linear as sampleDir.

    Flagstat / QC output must key off sampleId, not the mode leaf name.
    """
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons
    from methyl_alignment_qc.models.config import AlignmentGuardrailsConfig

    sample_id = "DPLST-051425-111148"
    mode_dir = tmp_path / sample_id / "linear"
    mode_dir.mkdir(parents=True)
    _write_text(mode_dir / f"{sample_id}.deduplicate_metrics.txt", _dedup_metrics_fixture())
    (mode_dir / f"{sample_id}.json").write_text(
        json.dumps(_parabricks_json_fixture(sample_id)), encoding="utf-8"
    )
    # Minimal BAM-shaped file so preflight does not fail on magic (gzip header).
    (mode_dir / f"{sample_id}.bam").write_bytes(b"\x1f\x8b" + b"\x00" * 64)

    flagstat_calls: list[tuple[str, str]] = []

    def _fake_flagstat(sample_dir, sid, force=False):
        flagstat_calls.append((str(sample_dir), sid))
        from methyl_alignment_qc.models.sample_qc import AlignmentFlagstat

        return AlignmentFlagstat(
            total_reads=100,
            mapped_reads=95,
            properly_paired_reads=92,
            supplementary_reads=0,
            secondary_reads=0,
            duplicate_reads=0,
            mapped_rate=0.95,
            properly_paired_rate=0.92,
            supplementary_rate=0.0,
        )

    monkeypatch.setattr("methyl_alignment_qc.core.writer.run_flagstat", _fake_flagstat)

    output_dir = tmp_path / "out"
    process_samples_to_qc_jsons(
        [str(mode_dir)],
        str(output_dir),
        validate_schema=True,
        sample_id=sample_id,
        alignment_guardrails=AlignmentGuardrailsConfig(
            enabled=True,
            flagstat_enabled=True,
            min_properly_paired_rate=0.90,
        ),
    )

    out = output_dir / f"{sample_id}.json"
    assert out.is_file()
    assert not (output_dir / "linear.json").exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["sample_id"] == sample_id
    assert flagstat_calls == [(str(mode_dir), sample_id)]
    details = payload["guardrails"]["details"]
    assert "properly_paired_rate" in details
    assert "BAM not found" not in str(details.get("properly_paired_rate", {}).get("message", ""))
    assert details["properly_paired_rate"]["pass"] is True


def _quality_yield_table() -> str:
    return (
        "TOTAL_READS\tPF_READS\tTOTAL_BASES\tPF_BASES\tQ20_BASES\tPF_Q20_BASES\t"
        "Q30_BASES\tPF_Q30_BASES\tQ20_EQUIVALENT_YIELD\tPF_Q20_EQUIVALENT_YIELD\n"
        "1000\t950\t120000\t100000\t110000\t98000\t95000\t90000\t200000\t190000\n"
    )


def test_find_qc_metrics_tar_packs_unpacked_directory(tmp_path: Path) -> None:
    from methyl_alignment_qc.core.writer import _find_qc_metrics_tar

    sample_dir = tmp_path / "S1"
    metrics = sample_dir / "S1.qc-metrics"
    metrics.mkdir(parents=True)
    (metrics / "quality_yield.txt").write_text(_quality_yield_table(), encoding="utf-8")
    tar_path = _find_qc_metrics_tar(sample_dir, "S1")
    assert tar_path is not None
    assert tar_path.is_file()
    assert tar_path.name == "S1.qc-metrics.tar"


def test_find_qc_metrics_tar_relinks_caas_blob(tmp_path: Path) -> None:
    import tarfile

    from methyl_alignment_qc.core.writer import (
        _find_qc_metrics_tar,
        _try_load_parabricks_metrics_payload,
    )

    sample_dir = tmp_path / "S1"
    blob_dir = sample_dir / ".caas" / "sample_parabricks_fq2bam" / "key-1"
    blob_dir.mkdir(parents=True)
    blob = blob_dir / "S1.qc-metrics.tar"
    qy = tmp_path / "quality_yield.txt"
    qy.write_text(_quality_yield_table(), encoding="utf-8")
    with tarfile.open(blob, "w") as tar:
        tar.add(qy, arcname="S1.qc-metrics/quality_yield.txt")

    tar_path = _find_qc_metrics_tar(sample_dir, "S1")
    assert tar_path is not None
    assert tar_path.is_file()
    loaded = _try_load_parabricks_metrics_payload(sample_dir, "S1")
    assert loaded is not None
    payload, source = loaded
    assert payload.get("quality_yield") is not None
    assert source.is_file()
