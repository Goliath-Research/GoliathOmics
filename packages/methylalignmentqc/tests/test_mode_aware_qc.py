"""Mode-aware methyl_qc: Parabricks vs methylGrapher WGBS paths."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from methyl_alignment_qc.core.metrics_family import MetricsFamily, detect_metrics_family
from methyl_alignment_qc.core.writer import build_sample_qc_v2_dict
from methyl_alignment_qc.models.config import (
    AlignmentGuardrailsConfig,
    CycleScreeningConfig,
    OptionalGuardrailsConfig,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _dedup_metrics() -> str:
    return (
        "## METRICS CLASS\tpicard.sam.markduplicates.MarkDuplicatesMetrics\n"
        "LIBRARY\tUNPAIRED_READS_EXAMINED\tREAD_PAIRS_EXAMINED\t"
        "SECONDARY_OR_SUPPLEMENTARY_RDS\tUNMAPPED_READS\tUNPAIRED_READ_DUPLICATES\t"
        "READ_PAIR_DUPLICATES\tREAD_PAIR_OPTICAL_DUPLICATES\tPERCENT_DUPLICATION\t"
        "ESTIMATED_LIBRARY_SIZE\n"
        "lib1\t0\t100\t0\t0\t0\t8\t1\t0.08\t1000\n"
        "## HISTOGRAM\tjava.lang.Double\n"
        "BIN\tVALUE\n"
        "1\t100\n"
        "2\t20\n"
    )


def _setup_wgbs_sample(tmp: Path, sample: str, *, with_linear_tar: bool = False) -> Path:
    d = tmp / sample
    d.mkdir(parents=True)
    _write_text(d / f"{sample}.deduplicate_metrics.txt", _dedup_metrics())
    # Non-empty BAM / GAF stubs (content not parsed when flagstat disabled)
    (d / f"{sample}.bam").write_bytes(b"BAM\x01fake")
    (d / f"{sample}.alignment.gaf").write_text("s\t*\t*\n", encoding="utf-8")
    prov = {
        "tool": "methylGrapher",
        "action": "sample.methylgrapher_wgbs_align",
        "sample_id": sample,
        "index_prefix": "/work/genomes/pangenome/idx",
        "directional": True,
        "asset_fingerprints": {"c2t.gbz": "abc123", "g2a.gbz": "def456"},
        "gaf": str(d / f"{sample}.alignment.gaf"),
        "bam": str(d / f"{sample}.bam"),
    }
    (d / f"{sample}.alignment_metrics.json").write_text(json.dumps(prov), encoding="utf-8")
    if with_linear_tar:
        # Stale linear Picard tar must be ignored when alignmentMode=pangenome_wgbs
        tar_path = d / f"{sample}.qc-metrics.tar"
        with tarfile.open(tar_path, "w") as tar:
            qy = d / "quality_yield.txt"
            qy.write_text(
                "TOTAL_READS\tPF_READS\tTOTAL_BASES\tPF_BASES\tQ20_BASES\tPF_Q20_BASES\t"
                "Q30_BASES\tPF_Q30_BASES\tQ20_EQUIVALENT_YIELD\tPF_Q20_EQUIVALENT_YIELD\n"
                "1000\t950\t100\t90\t80\t70\t60\t50\t40\t30\n",
                encoding="utf-8",
            )
            tar.add(qy, arcname=f"{sample}.qc-metrics/quality_yield.txt")
        (d / f"{sample}.json").write_text(
            json.dumps({"guardrails": {"overall_pass": True}}), encoding="utf-8"
        )
    return d


def test_detect_wgbs_refuses_without_provenance(tmp_path: Path):
    d = tmp_path / "s1"
    d.mkdir()
    _write_text(d / "s1.deduplicate_metrics.txt", _dedup_metrics())
    with pytest.raises(RuntimeError, match="alignment_metrics.json"):
        detect_metrics_family(d, "s1", alignment_mode="pangenome_wgbs", parabricks_available=True)


def test_detect_wgbs_ignores_parabricks_when_mode_set(tmp_path: Path):
    d = _setup_wgbs_sample(tmp_path, "s1", with_linear_tar=True)
    family, path, prov = detect_metrics_family(
        d, "s1", alignment_mode="pangenome_wgbs", parabricks_available=True
    )
    assert family == MetricsFamily.METHYLGRAPHER_WGBS
    assert path is not None
    assert prov is not None and prov["tool"] == "methylGrapher"


def test_build_qc_wgbs_path_no_parabricks_hard_fail(tmp_path: Path):
    sample = "HBCST-TEST"
    d = _setup_wgbs_sample(tmp_path, sample, with_linear_tar=True)
    payload = build_sample_qc_v2_dict(
        d,
        sample_id=sample,
        validate_schema=True,
        alignment_mode="pangenome_wgbs",
        alignment_guardrails=AlignmentGuardrailsConfig(enabled=True, flagstat_enabled=False),
        cycle_screening=CycleScreeningConfig(enabled=True),
        optional_guardrails=OptionalGuardrailsConfig(duplication_rate_max=0.5),
    )
    assert payload["sample_id"] == sample
    assert payload.get("quality_yield") is None
    assert payload["wgbs_align_metrics"]["tool"] == "methylGrapher"
    gr = payload["guardrails"]
    assert gr["metrics_family"] == "methylgrapher_wgbs"
    assert gr["overall_pass"] is True
    assert "wgbs_provenance" in gr["details"]
    assert "wgbs_gaf_present" in gr["details"]
    assert "wgbs_bam_present" in gr["details"]
    assert "pf_percent" not in gr["details"]
    screening = gr.get("screening") or {}
    assert screening.get("disposition") == "USE_CURRENT_ALIGNMENT"
    assert screening.get("quality_pattern") == "NO_CYCLE_METRICS"


def test_build_qc_wgbs_ignores_linear_properly_paired(tmp_path: Path, monkeypatch) -> None:
    """Mojo QC BAMs often report properly_paired_rate=0; must not block extract."""
    from methyl_alignment_qc.models.sample_qc import AlignmentFlagstat

    sample = "HBCST-PAIR0"
    d = _setup_wgbs_sample(tmp_path, sample)
    # Preflight accepts gzip magic; content unused because flagstat is mocked.
    (d / f"{sample}.bam").write_bytes(b"\x1f\x8b" + b"\x00" * 64)

    def _fake_flagstat(sample_dir, sid, force=False):
        return AlignmentFlagstat(
            total_reads=1000,
            mapped_reads=1000,
            properly_paired_reads=0,
            supplementary_reads=0,
            secondary_reads=0,
            duplicate_reads=0,
            mapped_rate=1.0,
            properly_paired_rate=0.0,
            supplementary_rate=0.0,
        )

    monkeypatch.setattr("methyl_alignment_qc.core.writer.run_flagstat", _fake_flagstat)
    payload = build_sample_qc_v2_dict(
        d,
        sample_id=sample,
        validate_schema=True,
        alignment_mode="pangenome_wgbs",
        alignment_guardrails=AlignmentGuardrailsConfig(
            enabled=True,
            flagstat_enabled=True,
            min_properly_paired_rate=0.80,
            min_mapping_rate=0.98,
        ),
        cycle_screening=CycleScreeningConfig(enabled=True),
    )
    gr = payload["guardrails"]
    assert gr["overall_pass"] is True
    assert "properly_paired_rate" not in (gr.get("details") or {})
    assert "mapping_rate" not in (gr.get("details") or {})


def test_build_qc_wgbs_fails_when_gaf_missing(tmp_path: Path):
    sample = "HBCST-FAIL"
    d = _setup_wgbs_sample(tmp_path, sample)
    (d / f"{sample}.alignment.gaf").unlink()
    payload = build_sample_qc_v2_dict(
        d,
        sample_id=sample,
        validate_schema=True,
        alignment_mode="pangenome_wgbs",
        alignment_guardrails=AlignmentGuardrailsConfig(enabled=False),
        cycle_screening=CycleScreeningConfig(enabled=False),
    )
    assert payload["guardrails"]["overall_pass"] is False
    assert payload["guardrails"]["details"]["wgbs_gaf_present"]["pass"] is False


def _write_full_picard_tar(sample_dir: Path, sample: str) -> None:
    """Minimal complete Picard tables so core WGBS Parabricks guardrails can run."""
    metrics = sample_dir / f"{sample}.qc-metrics"
    metrics.mkdir(exist_ok=True)
    (metrics / "quality_yield.txt").write_text(
        "TOTAL_READS\tPF_READS\tTOTAL_BASES\tPF_BASES\tQ20_BASES\tPF_Q20_BASES\t"
        "Q30_BASES\tPF_Q30_BASES\tQ20_EQUIVALENT_YIELD\tPF_Q20_EQUIVALENT_YIELD\n"
        "1000\t950\t151000\t143450\t140000\t135000\t130000\t125000\t120000\t115000\n",
        encoding="utf-8",
    )
    cycles = "\n".join(f"{i}\t38.0" for i in range(1, 51))
    (metrics / "mean_quality_by_cycle.txt").write_text(
        f"CYCLE\tMEAN_QUALITY\n{cycles}\n", encoding="utf-8"
    )
    (metrics / "gcbias_summary.txt").write_text(
        "ACCUMULATION_LEVEL\tREADS_USED\tGC\tWINDOWS\tREAD_STARTS\tMEAN_BASE_QUALITY\t"
        "NORMALIZED_COVERAGE\tERROR_BAR_WIDTH\tMIN_NORMALIZED_COVERAGE\t"
        "MAX_NORMALIZED_COVERAGE\tAT_DROPOUT\tGC_DROPOUT\n"
        "All Reads\tALL\t50\t100\t1000\t38\t1.0\t0.1\t0.9\t1.1\t1.0\t1.5\n",
        encoding="utf-8",
    )
    (metrics / "insert_size.txt").write_text(
        "MEDIAN_INSERT_SIZE\tMODE_INSERT_SIZE\tMEDIAN_ABSOLUTE_DEVIATION\tMIN_INSERT_SIZE\t"
        "MAX_INSERT_SIZE\tMEAN_INSERT_SIZE\tSTANDARD_DEVIATION\tREAD_PAIRS\tPAIR_ORIENTATION\t"
        "WIDTH_OF_10_PERCENT\tWIDTH_OF_20_PERCENT\tWIDTH_OF_30_PERCENT\tWIDTH_OF_40_PERCENT\t"
        "WIDTH_OF_50_PERCENT\tWIDTH_OF_60_PERCENT\tWIDTH_OF_70_PERCENT\tWIDTH_OF_80_PERCENT\t"
        "WIDTH_OF_90_PERCENT\tWIDTH_OF_95_PERCENT\tWIDTH_OF_99_PERCENT\n"
        "180\t175\t20\t50\t400\t185.0\t30.0\t500\tFR\t10\t20\t30\t40\t50\t60\t70\t80\t90\t95\t99\n"
        "## HISTOGRAM\tjava.lang.Integer\n"
        "insert_size\tAll_Reads.fr_count\n"
        "180\t100\n",
        encoding="utf-8",
    )
    (metrics / "sequencingArtifact.pre_adapter_summary_metrics.txt").write_text(
        "LIBRARY\tSAMPLE\tARTIFACT_NAME\tTOTAL_QSCORE\tWORST_CXT\tWORST_CXT_QSCORE\n"
        "lib\ts\tDeamination\t25\tC\t25\n"
        "lib\ts\tOxoG\t30\tG\t30\n",
        encoding="utf-8",
    )
    tar_path = sample_dir / f"{sample}.qc-metrics.tar"
    with tarfile.open(tar_path, "w") as tar:
        for p in sorted(metrics.iterdir()):
            if p.is_file():
                tar.add(p, arcname=f"{sample}.qc-metrics/{p.name}")


def test_build_qc_wgbs_picard_enrichment_when_flagged(tmp_path: Path):
    """Provenance collectmultiplemetrics=true + full Picard tar enriches QC."""
    sample = "HBCST-PICARD"
    d = _setup_wgbs_sample(tmp_path, sample)
    _write_full_picard_tar(d, sample)
    prov_path = d / f"{sample}.alignment_metrics.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    prov["collectmultiplemetrics"] = True
    prov_path.write_text(json.dumps(prov), encoding="utf-8")

    payload = build_sample_qc_v2_dict(
        d,
        sample_id=sample,
        validate_schema=True,
        alignment_mode="pangenome_wgbs",
        alignment_guardrails=AlignmentGuardrailsConfig(enabled=True, flagstat_enabled=False),
        cycle_screening=CycleScreeningConfig(enabled=True),
    )
    assert payload.get("quality_yield") is not None
    assert payload["wgbs_align_metrics"]["collectmultiplemetrics"] is True
    gr = payload["guardrails"]
    assert gr["metrics_family"] == "methylgrapher_wgbs"
    assert gr.get("picard_enrichment") is True
    assert "pf_percent" in gr["details"]
    assert "wgbs_provenance" in gr["details"]
    screening = gr.get("screening") or {}
    assert screening.get("disposition") in {
        "USE_CURRENT_ALIGNMENT",
        "REALIGN_READ2_TRIM",
        "NOT_FIXABLE",
    }


def test_detect_ambiguous_artifacts_require_alignment_mode(tmp_path: Path):
    d = _setup_wgbs_sample(tmp_path, "s1", with_linear_tar=True)
    with pytest.raises(RuntimeError, match="Ambiguous alignment QC"):
        detect_metrics_family(d, "s1", alignment_mode=None, parabricks_available=True)


def test_detect_mojo_linear_family(tmp_path: Path):
    sample = "mojo1"
    d = tmp_path / sample
    d.mkdir()
    _write_text(d / f"{sample}.deduplicate_metrics.txt", _dedup_metrics())
    (d / f"{sample}.bam").write_bytes(b"BAM\x01fake")
    mojo_json = {
        "sample_id": sample,
        "engine": "mojo_fq2bam_meth",
        "metrics_source": "samtools+placeholders",
        "placeholder_fields": ["gc_bias_summary"],
        "quality_yield": {
            "total_reads": 1000,
            "pf_reads": 950,
            "pf_bases": 100000,
            "pf_q30_bases": 90000,
        },
        "alignment_summary": {"total_reads": 1000, "mapped_reads": 900, "mapped_rate": 0.9},
        "mean_quality_by_cycle": {"mean_quality": [36.0] * 50},
        "gc_bias_summary": {"at_dropout": 1.0, "gc_dropout": 1.0},
        "insert_size_metrics": {"median_insert_size": 200.0},
        "pre_adapter_summaries": {
            "ARTIFACT_NAME": ["Deamination", "OxoG"],
            "TOTAL_QSCORE": [5, 40],
        },
    }
    (d / f"{sample}.json").write_text(json.dumps(mojo_json), encoding="utf-8")
    family, _, _ = detect_metrics_family(
        d, sample, alignment_mode="linear", parabricks_available=False
    )
    assert family == MetricsFamily.MOJO_LINEAR


def test_build_qc_mojo_linear_skips_placeholder_hard_fails(tmp_path: Path):
    sample = "mojo2"
    d = tmp_path / sample
    d.mkdir()
    _write_text(d / f"{sample}.deduplicate_metrics.txt", _dedup_metrics())
    (d / f"{sample}.bam").write_bytes(b"BAM\x01fake")
    mojo_json = {
        "sample_id": sample,
        "engine": "mojo_fq2bam_meth",
        "metrics_source": "samtools+placeholders",
        "placeholder_fields": ["gc_bias_summary", "pre_adapter_summaries.TOTAL_QSCORE"],
        "quality_yield": {
            "total_reads": 1000,
            "pf_reads": 950,
            "pf_bases": 100000,
            "pf_q30_bases": 90000,
        },
        "alignment_summary": {"total_reads": 1000, "mapped_reads": 900, "mapped_rate": 0.9},
        "mean_quality_by_cycle": {"mean_quality": [36.0] * 50},
        "gc_bias_summary": {"at_dropout": 99.0, "gc_dropout": 99.0},
        "insert_size_metrics": {"median_insert_size": 10.0},
        "pre_adapter_summaries": {
            "ARTIFACT_NAME": ["Deamination", "OxoG"],
            "TOTAL_QSCORE": [99, 1],
        },
    }
    (d / f"{sample}.json").write_text(json.dumps(mojo_json), encoding="utf-8")
    payload = build_sample_qc_v2_dict(
        d,
        sample_id=sample,
        validate_schema=True,
        alignment_mode="linear",
        alignment_guardrails=AlignmentGuardrailsConfig(enabled=True, flagstat_enabled=False),
        cycle_screening=CycleScreeningConfig(enabled=True),
    )
    gr = payload["guardrails"]
    assert gr["metrics_family"] == "mojo_linear"
    assert gr["overall_pass"] is True
    assert "mojo_placeholder_fields_skipped" in gr["details"]
    assert "gc_dropout" not in gr["details"]
    screening = gr.get("screening") or {}
    # Cycle screening disabled for placeholder constant series
    assert screening.get("disposition") in {
        None,
        "USE_CURRENT_ALIGNMENT",
        "NO_CYCLE_METRICS",
    } or screening.get("quality_pattern") in {None, "NO_CYCLE_METRICS", "OK"}


def test_build_qc_wgbs_ignores_picard_tar_without_flag(tmp_path: Path):
    """Stale linear Picard tar must not enrich without collectmultiplemetrics flag."""
    sample = "HBCST-STALE"
    d = _setup_wgbs_sample(tmp_path, sample, with_linear_tar=True)
    payload = build_sample_qc_v2_dict(
        d,
        sample_id=sample,
        validate_schema=True,
        alignment_mode="pangenome_wgbs",
        alignment_guardrails=AlignmentGuardrailsConfig(enabled=False),
        cycle_screening=CycleScreeningConfig(enabled=False),
    )
    assert payload.get("quality_yield") is None
    assert payload["guardrails"].get("picard_enrichment") is not True
    assert "pf_percent" not in payload["guardrails"]["details"]
