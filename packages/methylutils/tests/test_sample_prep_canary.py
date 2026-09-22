"""Unit tests for SamplePrep real-data canary config + validators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_utils.test_data_registry import (
    SamplePrepCanaryConfig,
    TestDataRegistry,
    load_sample_prep_canary,
)
from methyl_utils.testing.sample_prep_canary import (
    CheckResult,
    ModeReport,
    build_qualification_report,
    compare_linear_vs_wgbs,
    validate_mode_artifacts,
    write_junit,
    write_markdown_summary,
)


def test_canary_config_parses_example() -> None:
    root = Path(__file__).resolve().parents[3]
    example = root / "tests" / "real_data" / "sample_prep_canary" / "registry.example.json"
    raw = json.loads(example.read_text(encoding="utf-8"))
    cfg = SamplePrepCanaryConfig.model_validate(raw["sample_prep_canary"])
    assert cfg.source is not None
    assert cfg.source.sra_run == "SRR28293403"
    assert cfg.subset is not None
    assert "pangenome_wgbs" in (cfg.modes or [])


def test_registry_accepts_sample_prep_canary() -> None:
    reg = TestDataRegistry.model_validate(
        {
            "sample_prep_canary": {
                "sample_id": "canary-x",
                "modes": ["linear"],
                "source": {"sra_run": "SRR28293403"},
            }
        }
    )
    assert reg.sample_prep_canary is not None
    assert reg.sample_prep_canary.sample_id == "canary-x"


def test_load_sample_prep_canary_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "sample_id": "from-env",
        "modes": ["linear", "pangenome_wgbs"],
        "subset": {
            "prefix": "canary/subset/",
            "r1_name": "a_1.fastq.gz",
            "r2_name": "a_2.fastq.gz",
            "r1_sha256": "abc",
            "r2_sha256": "def",
        },
    }
    path = tmp_path / "canary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("METHYL_SAMPLE_PREP_CANARY_CONFIG", str(path))
    cfg = load_sample_prep_canary()
    assert cfg is not None
    assert cfg.sample_id == "from-env"


def test_validate_mode_artifacts_synthetic(tmp_path: Path) -> None:
    sample_id = "S1"
    sample_dir = tmp_path / sample_id
    sample_dir.mkdir()
    (sample_dir / f"{sample_id}.bam").write_bytes(b"bam")
    qc = {
        "guardrails": {"overall_pass": True},
        "screening": {"disposition": "PASS"},
        "summary_stats": {"mapping_rate": 0.9, "duplication_rate": 0.1},
    }
    (sample_dir / f"{sample_id}.alignment_qc.json").write_text(json.dumps(qc), encoding="utf-8")
    (sample_dir / "21-CG.h5").write_bytes(b"h5")
    man = {
        "metadata": {"schema_name": "methylextractor.extraction_manifest", "extractor": "MethylExtractor"},
        "summary": {"cpg_weighted_mean_coverage": 12.0, "cpg_sites": 1000},
    }
    (sample_dir / f"{sample_id}.extraction_manifest.json").write_text(
        json.dumps(man), encoding="utf-8"
    )
    eqc = {"guardrails": {"overall_pass": True}}
    (sample_dir / f"{sample_id}.extraction_qc.json").write_text(json.dumps(eqc), encoding="utf-8")
    (sample_dir / f"{sample_id}.sample_prep_log.jsonl").write_text("{}\n", encoding="utf-8")

    report = validate_mode_artifacts(
        mode="linear",
        sample_id=sample_id,
        sample_dir=sample_dir,
        observed_actions=["sample.parabricks_fq2bam", "sample.methyl_extract"],
    )
    assert report.ok
    assert report.metrics["mapping_rate"] == 0.9


def test_validate_methylgrapher_manifest_requires_graph_assets(tmp_path: Path) -> None:
    sample_id = "S1"
    sample_dir = tmp_path / sample_id
    sample_dir.mkdir()
    (sample_dir / f"{sample_id}.bam").write_bytes(b"bam")
    (sample_dir / f"{sample_id}.alignment.gaf").write_text("gaf\n", encoding="utf-8")
    (sample_dir / f"{sample_id}.alignment_qc.json").write_text(
        json.dumps({"guardrails": {"overall_pass": True}, "summary_stats": {}}),
        encoding="utf-8",
    )
    (sample_dir / "1-CG.h5").write_bytes(b"h5")
    (sample_dir / f"{sample_id}.extraction_manifest.json").write_text(
        json.dumps(
            {
                "metadata": {"schema_name": "methylextractor.extraction_manifest"},
                "summary": {"cpg_weighted_mean_coverage": 10.0, "cpg_sites": 500},
                "graph_assets": {"bundle": "d9-bs"},
            }
        ),
        encoding="utf-8",
    )
    (sample_dir / f"{sample_id}.extraction_qc.json").write_text(
        json.dumps({"guardrails": {"overall_pass": True}}), encoding="utf-8"
    )
    (sample_dir / f"{sample_id}.sample_prep_log.jsonl").write_text("{}\n", encoding="utf-8")
    report = validate_mode_artifacts(mode="pangenome_wgbs", sample_id=sample_id, sample_dir=sample_dir)
    assert any(c.name == "canonical_extraction_manifest" and c.ok for c in report.checks)
    assert any(c.name == "graph_assets_fingerprint" and c.ok for c in report.checks)


def test_compare_linear_vs_wgbs_thresholds() -> None:
    linear = ModeReport(mode="linear", sample_id="L", sample_dir=Path("/tmp/L"))
    linear.metrics = {
        "mapping_rate": 0.90,
        "duplication_rate": 0.10,
        "cpg_weighted_mean_coverage": 20.0,
        "cpg_sites": 1000,
    }
    wgbs = ModeReport(mode="pangenome_wgbs", sample_id="W", sample_dir=Path("/tmp/W"))
    wgbs.metrics = {
        "mapping_rate": 0.88,
        "duplication_rate": 0.12,
        "cpg_weighted_mean_coverage": 18.0,
        "cpg_sites": 900,
    }
    from methyl_utils.test_data_registry import SamplePrepCanaryThresholds

    thr = SamplePrepCanaryThresholds(
        mapping_rate_delta=0.05,
        duplication_rate_delta=0.05,
        mean_coverage_delta=5.0,
        cpg_sites_min_fraction_of_linear=0.8,
    )
    checks = compare_linear_vs_wgbs(linear, wgbs, thresholds=thr)
    assert checks
    assert all(c.ok for c in checks)


def test_reports_write(tmp_path: Path) -> None:
    cfg = SamplePrepCanaryConfig.model_validate({"sample_id": "canary", "modes": ["linear"]})
    mode = ModeReport(mode="linear", sample_id="canary-linear", sample_dir=tmp_path)
    mode.checks.append(CheckResult("bam_present", True, "ok"))
    report = build_qualification_report(
        config=cfg,
        tier="subset",
        mode_reports=[mode],
        comparison_checks=[],
        meta={"k": 1},
    )
    junit = tmp_path / "out.junit.xml"
    md = tmp_path / "out.md"
    write_junit(report, junit)
    write_markdown_summary(report, md)
    assert junit.is_file()
    assert "SamplePrep real-data canary" in md.read_text(encoding="utf-8")


def test_compute_cg_overlap_stats(tmp_path: Path) -> None:
    import h5py
    import numpy as np

    from methyl_utils.testing.sample_prep_canary import compute_cg_overlap_stats

    def write_cg(path: Path, rows):
        dt = np.dtype([("pos", "<u4"), ("mC", "<u2"), ("uC", "<u2"), ("tnc", "u1")])
        data = np.empty(len(rows), dtype=dt)
        for i, (pos, mc, uc) in enumerate(rows):
            data[i] = (pos, mc, uc, 1)
        with h5py.File(path, "w") as f:
            f.create_dataset("methylation_data", data=data)

    lin = tmp_path / "linear"
    wgbs = tmp_path / "wgbs"
    lin.mkdir()
    wgbs.mkdir()
    write_cg(lin / "1-CG.h5", [(100, 8, 2), (200, 0, 10), (300, 5, 5)])
    write_cg(wgbs / "1-CG.h5", [(100, 7, 3), (200, 1, 9), (400, 4, 4)])
    stats = compute_cg_overlap_stats(lin, wgbs, chromosomes=["1"])
    assert stats["linear_sites"] == 3
    assert stats["wgbs_sites"] == 3
    assert stats["shared_sites"] == 2
    assert abs(stats["overlap_recall_of_linear"] - 2 / 3) < 1e-9
    assert stats["overlap_meth_pearson"] is not None
    assert stats["overlap_mean_abs_meth_delta"] is not None


def test_overlap_median_cov_ratio_averages_middle_pair(tmp_path: Path) -> None:
    import h5py
    import numpy as np

    from methyl_utils.testing.sample_prep_canary import compute_cg_overlap_stats

    def write_cg(path: Path, rows):
        dt = np.dtype([("pos", "<u4"), ("mC", "<u2"), ("uC", "<u2"), ("tnc", "u1")])
        data = np.empty(len(rows), dtype=dt)
        for i, (pos, mc, uc) in enumerate(rows):
            data[i] = (pos, mc, uc, 1)
        with h5py.File(path, "w") as f:
            f.create_dataset("methylation_data", data=data)

    lin = tmp_path / "linear"
    wgbs = tmp_path / "wgbs"
    lin.mkdir()
    wgbs.mkdir()
    # Four shared sites with coverage ratios 0.5, 1.0, 2.0, 4.0 → median 1.5.
    write_cg(lin / "1-CG.h5", [(100, 8, 2), (200, 5, 5), (300, 5, 5), (400, 2, 8)])
    write_cg(wgbs / "1-CG.h5", [(100, 4, 1), (200, 5, 5), (300, 10, 10), (400, 10, 30)])
    stats = compute_cg_overlap_stats(lin, wgbs, chromosomes=["1"])
    assert stats["shared_sites"] == 4
    assert stats["overlap_median_cov_ratio"] == pytest.approx(1.5)
