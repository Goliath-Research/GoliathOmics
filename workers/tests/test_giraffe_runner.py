"""Tests for Parabricks giraffe + collectmultiplemetrics Docker runner."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from methyl_worker import giraffe_runner as runner


def _write_graph_bundle(root: Path) -> dict[str, str]:
    graph_dir = root / "pangenome"
    graph_dir.mkdir(parents=True)
    paths = {
        "gbz": graph_dir / "graph.gbz",
        "dist": graph_dir / "graph.dist",
        "min": graph_dir / "graph.min",
        "zipcodes": graph_dir / "graph.zipcodes",
        "ref_paths": graph_dir / "graph.paths.sub",
        "linear_ref_fasta": root / "genomes" / "GRCh38.fa",
    }
    paths["linear_ref_fasta"].parent.mkdir(parents=True, exist_ok=True)
    for p in paths.values():
        p.write_text("x")
    return {k: str(v) for k, v in paths.items()}


def test_mount_root_excludes_linear_ref_fasta_parent(tmp_path: Path) -> None:
    """Production layout: graph under pangenome/, linear FASTA under linear/."""
    pangenome_dir = tmp_path / "work" / "genomes" / "pangenome" / "GRCh38" / "d9" / "1.70"
    linear_dir = tmp_path / "work" / "genomes" / "linear" / "GRCh38" / "ensembl-116"
    pangenome_dir.mkdir(parents=True)
    linear_dir.mkdir(parents=True)
    graph = runner.PangenomeGraphBundle(
        gbz=pangenome_dir / "graph.gbz",
        dist=pangenome_dir / "graph.dist",
        minimizer=pangenome_dir / "graph.min",
        zipcodes=pangenome_dir / "graph.zipcodes",
        ref_paths=pangenome_dir / "graph.paths.sub",
        linear_ref_fasta=linear_dir / "GRCh38.fa",
    )
    for p in (
        graph.gbz,
        graph.dist,
        graph.minimizer,
        graph.zipcodes,
        graph.ref_paths,
        graph.linear_ref_fasta,
    ):
        p.write_text("x")

    assert graph.mount_root.resolve() == pangenome_dir.resolve()


def test_build_giraffe_docker_command_mounts_graph_and_ref_paths(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    bundle_paths = _write_graph_bundle(tmp_path)
    graph = runner.PangenomeGraphBundle(
        gbz=Path(bundle_paths["gbz"]),
        dist=Path(bundle_paths["dist"]),
        minimizer=Path(bundle_paths["min"]),
        zipcodes=Path(bundle_paths["zipcodes"]),
        ref_paths=Path(bundle_paths["ref_paths"]),
        linear_ref_fasta=Path(bundle_paths["linear_ref_fasta"]),
    )
    cfg = runner.ParabricksConfig(
        image="nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1",
        gpu_flags=("--gpus", "all"),
        bwa_threads=8,
        extra_docker_args=(),
        cleanup_tmp=True,
    )
    paths = runner._resolve_paths(sample_dir, "S1", graph.linear_ref_fasta)
    fq1 = sample_dir / "S1_1.fastq.gz"
    fq2 = sample_dir / "S1_2.fastq.gz"
    fq1.write_bytes(b"1")
    fq2.write_bytes(b"2")

    with patch.object(runner, "_docker_bin", return_value="/usr/bin/docker"):
        cmd = runner._build_giraffe_docker_command(cfg, paths, graph, [fq1, fq2])

    assert "pbrun" in cmd
    assert "giraffe" in cmd
    assert "--entrypoint" in cmd
    assert "umask 000" in " ".join(cmd)
    assert f"{graph.mount_root.resolve()}:/pangenome:ro" in cmd
    assert graph.mount_root.resolve() == (tmp_path / "pangenome").resolve()
    assert "--gbz-name=/pangenome/graph.gbz" in cmd
    assert "--ref-paths=/pangenome/graph.paths.sub" in cmd
    assert "--out-bam=/outputdir/S1.bam" in cmd
    assert "--out-duplicate-metrics=/outputdir/S1.deduplicate_metrics.txt" in cmd


def test_build_collect_metrics_docker_command(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S2"
    sample_dir.mkdir()
    ref = tmp_path / "genomes" / "GRCh38.fa"
    ref.parent.mkdir(parents=True)
    ref.write_text(">ref\n")
    cfg = runner.ParabricksConfig(
        image="nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1",
        gpu_flags=("--gpus", "all"),
        bwa_threads=8,
        extra_docker_args=(),
        cleanup_tmp=True,
    )
    paths = runner._resolve_paths(sample_dir, "S2", ref)
    (sample_dir / "S2.bam").write_bytes(b"BAM")

    with patch.object(runner, "_docker_bin", return_value="/usr/bin/docker"):
        cmd = runner._build_collect_metrics_docker_command(cfg, paths, ref)

    assert "collectmultiplemetrics" in cmd
    assert "--entrypoint" in cmd
    assert "umask 000" in " ".join(cmd)
    assert "--gen-all-metrics" in cmd
    assert f"--ref=/genomes/{ref.name}" in cmd
    assert "--bam=/workdir/S2.bam" in cmd
    assert "--out-qc-metrics-dir=/outputdir/S2.qc-metrics" in cmd


def test_run_giraffe_align_invokes_giraffe_then_metrics(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S3"
    sample_dir.mkdir()
    bundle_paths = _write_graph_bundle(tmp_path)
    site = {"pangenome_genome": bundle_paths}
    site_path = tmp_path / "site.json"
    site_path.write_text(json.dumps(site), encoding="utf-8")
    (sample_dir / "S3_1.fastq.gz").write_bytes(b"1")
    (sample_dir / "S3_2.fastq.gz").write_bytes(b"2")

    calls: list[str] = []

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if " giraffe " in joined or joined.endswith(" giraffe"):
            calls.append("giraffe")
            (sample_dir / "S3.bam").write_bytes(b"BAM")
            (sample_dir / "S3.deduplicate_metrics.txt").write_text("ok")
        elif "collectmultiplemetrics" in joined:
            calls.append("metrics")
            qc_dir = sample_dir / "S3.qc-metrics"
            qc_dir.mkdir()
            (qc_dir / "quality_yield.txt").write_text("CYCLE\t1\n")
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch.dict(
        "os.environ",
        {"METHYL_PARABRICKS_IMAGE": "nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1"},
    ):
        with patch("methyl_worker.capabilities._gpu_available", return_value=True):
            with patch("methyl_worker.giraffe_runner.subprocess.run", side_effect=fake_run):
                out = runner.run_giraffe_align(
                    sample_id="S3",
                    sample_dir=sample_dir,
                    site_path=site_path,
                )

    assert calls == ["giraffe", "metrics"]
    assert out["bamPath"] == str(sample_dir / "S3.bam")
    assert out["qcMetricsTar"] == str(sample_dir / "S3.qc-metrics.tar")


def test_resolve_pangenome_graph_bundle_missing_key(tmp_path: Path) -> None:
    site_path = tmp_path / "site.json"
    site_path.write_text(json.dumps({"pangenome_genome": {"gbz": "/x.gbz"}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="pangenome_genome.dist"):
        runner.resolve_pangenome_graph_bundle(site_path=site_path)


def test_giraffe_qc_metrics_tar_parseable_by_methyl_qc(tmp_path: Path) -> None:
    """Giraffe runner qc-metrics.tar uses the same tables methyl-qc expects."""
    from methyl_alignment_qc.core.writer import _build_parabricks_payload_from_qc_tar

    sample_dir = tmp_path / "S4"
    sample_dir.mkdir()
    qc_dir = sample_dir / "S4.qc-metrics"
    qc_dir.mkdir()
    (qc_dir / "quality_yield.txt").write_text(
        "CATEGORY\tTOTAL_READS\tPF_READS\tTOTAL_BASES\tPF_BASES\t"
        "Q20_BASES\tPF_Q20_BASES\tQ30_BASES\tPF_Q30_BASES\t"
        "Q20_EQUIVALENT_YIELD\tPF_Q20_EQUIVALENT_YIELD\n"
        "ALL\t1000\t950\t120000\t100000\t110000\t98000\t95000\t85000\t200000\t190000\n",
        encoding="utf-8",
    )
    (qc_dir / "mean_quality_by_cycle.txt").write_text(
        "CYCLE\tMEAN_QUALITY\n1\t35\n2\t35\n",
        encoding="utf-8",
    )
    (qc_dir / "gcbias_summary.txt").write_text(
        "ACCUMULATION_LEVEL\tREADS_USED\tAT_DROPOUT\tGC_DROPOUT\n"
        "All Reads\t950\t1.0\t2.0\n",
        encoding="utf-8",
    )
    (qc_dir / "insert_size.txt").write_text(
        "MEDIAN_INSERT_SIZE\tMODE_INSERT_SIZE\tMEAN_INSERT_SIZE\tSTANDARD_DEVIATION\t"
        "READ_PAIRS\tWIDTH_OF_10_PERCENT\tWIDTH_OF_20_PERCENT\tWIDTH_OF_30_PERCENT\t"
        "WIDTH_OF_40_PERCENT\tWIDTH_OF_50_PERCENT\tWIDTH_OF_60_PERCENT\tWIDTH_OF_70_PERCENT\t"
        "WIDTH_OF_80_PERCENT\tWIDTH_OF_90_PERCENT\tWIDTH_OF_95_PERCENT\tWIDTH_OF_99_PERCENT\n"
        "200\t200\t200\t50\t475\t10\t20\t30\t40\t50\t60\t70\t80\t90\t95\t99\n",
        encoding="utf-8",
    )
    (qc_dir / "sequencingArtifact.pre_adapter_summary_metrics.txt").write_text(
        "ARTIFACT_NAME\tTOTAL_QSCORE\tWORST_CXT\tWORST_CXT_QSCORE\n"
        "Deamination\t25\tCCT\t20\nOxoG\t30\tGGT\t28\n",
        encoding="utf-8",
    )
    paths = runner._resolve_paths(sample_dir, "S4", tmp_path / "GRCh38.fa")
    runner._package_qc_metrics(paths)

    payload = _build_parabricks_payload_from_qc_tar(sample_dir, "S4")
    assert payload is not None
    assert "quality_yield" in payload
    assert payload["quality_yield"]["pf_reads"] == 950
