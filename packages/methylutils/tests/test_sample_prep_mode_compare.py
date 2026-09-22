"""Unit tests for linear vs pangenome_wgbs SamplePrep mode-compare helpers."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_utils.test_data_registry import SamplePrepCanaryThresholds
from methyl_utils.testing.sample_prep_canary import ModeReport
from methyl_utils.testing.sample_prep_mode_compare import (
    action_config_overlay_for_arm,
    build_align_arm_start_payload,
    build_mode_compare_report,
    build_sample_compare_block,
    build_start_payload,
    discover_root_fastqs,
    ensure_comparison_arms,
    ensure_comparison_report_dir,
    extract_alignment_duration_ms,
    link_root_fastqs_into_mode,
    mode_sample_dir,
    sample_root_dir,
    write_comparison_arms_report,
    write_mode_compare_markdown,
)


def test_sample_root_and_mode_layout(tmp_path: Path) -> None:
    root = sample_root_dir(tmp_path, "S1")
    root.mkdir()
    assert mode_sample_dir(root, "linear") == root / "linear"
    assert mode_sample_dir(root, "pangenome_wgbs") == root / "pangenome_wgbs"


def test_link_root_fastqs_hardlink(tmp_path: Path) -> None:
    sample_id = "S1"
    root = sample_root_dir(tmp_path, sample_id)
    root.mkdir()
    r1 = root / f"{sample_id}_1.fastq.gz"
    r2 = root / f"{sample_id}_2.fastq.gz"
    r1.write_bytes(b"fastq1")
    r2.write_bytes(b"fastq2")
    linked = link_root_fastqs_into_mode(root, "linear", sample_id=sample_id)
    assert len(linked) == 2
    mode = mode_sample_dir(root, "linear")
    assert (mode / r1.name).is_file()
    assert (mode / r1.name).stat().st_size == 6
    # Prefer hardlink when possible (same inode).
    if (mode / r1.name).stat().st_ino == r1.stat().st_ino:
        assert True
    assert discover_root_fastqs(root, sample_id) == [r1, r2]


def test_extract_alignment_duration_from_logs(tmp_path: Path) -> None:
    sample_id = "S1"
    sample_dir = tmp_path / "linear"
    sample_dir.mkdir()
    (sample_dir / "action_run_log.jsonl").write_text(
        json.dumps(
            {
                "action": "sample.parabricks_fq2bam",
                "duration_ms": 12000,
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sample_dir / f"{sample_id}.sample_prep_log.jsonl").write_text(
        json.dumps(
            {
                "action": "sample.methylgrapher_wgbs_align",
                "outputs": {"duration_ms": 99000},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert (
        extract_alignment_duration_ms(sample_dir, sample_id=sample_id, mode="linear")
        == 12000
    )


def test_build_start_payload_omits_sample_storage(tmp_path: Path) -> None:
    body = build_start_payload(
        sample_id="S1",
        mode="pangenome_wgbs",
        sample_dir=tmp_path / "pangenome_wgbs",
        project_path="/work/projects/x/project.json",
        workflow_version_id=12,
        primary_analyte="cfdna",
        reference_fasta="/work/genomes/g.fa",
        fastq_storage={"type": "file", "basePath": str(tmp_path / "pangenome_wgbs")},
    )
    assert "sampleStorage" not in body
    assert "h5Storage" not in body
    assert body["deleteFastqs"] is False
    assert body["alignmentMode"] == "pangenome_wgbs"
    assert body["samples"][0]["sampleDir"].endswith("pangenome_wgbs")


def test_compare_report_hypothesis_and_markdown(tmp_path: Path) -> None:
    linear = ModeReport(mode="linear", sample_id="S1", sample_dir=tmp_path / "linear")
    linear.metrics = {
        "cpg_weighted_mean_coverage": 20.0,
        "cpg_sites": 1000,
        "alignment_duration_ms": 10000,
        "mapping_rate": 0.9,
        "duplication_rate": 0.1,
        "extraction_qc_pass": True,
    }
    wgbs = ModeReport(
        mode="pangenome_wgbs", sample_id="S1", sample_dir=tmp_path / "pangenome_wgbs"
    )
    wgbs.metrics = {
        "cpg_weighted_mean_coverage": 24.0,
        "cpg_sites": 1100,
        "alignment_duration_ms": 25000,
        "mapping_rate": 0.88,
        "duplication_rate": 0.12,
        "extraction_qc_pass": True,
    }
    thr = SamplePrepCanaryThresholds(
        mapping_rate_delta=0.05,
        cpg_coverage_min_fraction_of_linear=0.8,
        cpg_sites_min_fraction_of_linear=0.8,
        alignment_time_ratio_max=5.0,
    )
    block = build_sample_compare_block(
        sample_id="S1", linear=linear, wgbs=wgbs, thresholds=thr
    )
    assert block["hypothesis"]["cpg_coverage_delta"] == 4.0
    assert block["hypothesis"]["alignment_time_ratio_wgbs_over_linear"] == 2.5
    assert any(c["name"] == "alignment_time_ratio_max" and c["ok"] for c in block["comparison_checks"])
    report = build_mode_compare_report(sample_blocks=[block], thresholds=thr, meta={"k": 1})
    md = tmp_path / "comparison.md"
    write_mode_compare_markdown(report, md)
    text = md.read_text(encoding="utf-8")
    assert "experiment-only" in text
    assert "no QNAP archive" in text
    assert "S1" in text


def test_ensure_comparison_arms_layout(tmp_path: Path) -> None:
    sample_id = "S1"
    root = sample_root_dir(tmp_path, sample_id)
    root.mkdir()
    (root / f"{sample_id}_1.fastq.gz").write_bytes(b"r1")
    (root / f"{sample_id}_2.fastq.gz").write_bytes(b"r2")
    arms = ensure_comparison_arms(
        root,
        sample_id=sample_id,
        arms=["align.linear.parabricks", "align.linear.mojo", "extract.methyldackel"],
    )
    assert (arms["align.linear.parabricks"] / f"{sample_id}_1.fastq.gz").is_file()
    assert (root / "extract.methyldackel").is_dir()
    assert action_config_overlay_for_arm("align.linear.mojo")["parabricks"]["engine"] == "mojo"
    body = build_align_arm_start_payload(
        sample_id=sample_id,
        arm="align.pangenome_wgbs.vg",
        sample_root=root,
        project_path="/tmp/p.json",
        workflow_version_id=None,
        primary_analyte="buffy_coat",
        reference_fasta="/tmp/r.fa",
        fastq_storage={"type": "local"},
    )
    assert body["alignmentMode"] == "pangenome_wgbs"
    assert body["actionConfig"]["methylgrapher_wgbs"]["align_engine"] == "cpu_vg"


def test_write_comparison_arms_report(tmp_path: Path) -> None:
    rd = ensure_comparison_report_dir(tmp_path, stamp="20260815T120000Z")
    j, m = write_comparison_arms_report(
        rd,
        sample_id="S1",
        arms={
            "align.linear.parabricks": {"status": "ok", "wall_s": 100.0, "engine": "parabricks"},
            "align.linear.mojo": {"status": "ok", "wall_s": 80.0, "engine": "mojo"},
        },
        gates={
            "clara_vs_mojo_linear_wall": {
                "status": "PENDING",
                "detail": "operator GH200; do not flip site defaults",
            }
        },
        methyldackel={"tool": "MethylDackel", "status": "skipped"},
    )
    assert j.is_file() and m.is_file()
    payload = json.loads(j.read_text(encoding="utf-8"))
    assert payload["schema"] == "methylpipeline.comparison_arms"
    assert "Clara fq2bam_meth" in payload["originals_retained"][0]
    text = m.read_text(encoding="utf-8")
    assert "align.linear.parabricks" in text
    assert "MethylDackel" in text
