"""Unit tests for methylGrapher WGBS pangenome SamplePrep path."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from methyl_utils.action_config_resolver import resolve_methylgrapher_wgbs_genome
from methyl_worker.methylgrapher_wgbs_runner import (
    _flatten_qc_metrics_dir,
    _package_qc_tar,
    _restore_original_sequences,
    _run,
    _write_bs_converted_fastq,
    assert_methylcall_assets,
    build_align_command,
    build_grch38_segment_offsets_from_gfa,
    build_methylcall_command,
    build_qc_bam_command,
    ensure_qc_bam_read_group,
    project_graph_cpg_to_linear_tsv,
    resolve_wgbs_bundle_from_resolved,
    run_methylgrapher_wgbs_align,
    run_methylgrapher_wgbs_extract,
    share_work_path,
)
from methyl_worker.task_models.sample_prep_models import (
    MethylGrapherWgbsAlignTaskInput,
    MethylGrapherWgbsExtractTaskInput,
    MethylGrapherWgbsStepConfig,
)

_HAS_SAMTOOLS = shutil.which("samtools") is not None


def _touch_bundle(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "c2t": {
            "gbz": root / "c2t.gbz",
            "dist": root / "c2t.dist",
            "min": root / "c2t.min",
            "zipcodes": root / "c2t.zip",
        },
        "g2a": {
            "gbz": root / "g2a.gbz",
            "dist": root / "g2a.dist",
            "min": root / "g2a.min",
            "zipcodes": root / "g2a.zip",
        },
        "ref_paths": root / "ref.paths",
        "cpg_tsv": root / "cpg.tsv",
        "linear_ref_fasta": root / "GRCh38.fa",
        "original_gbz": root / "orig.gbz",
        "index_prefix": str(root / "hprc-d9-bs"),
        "directional": True,
        "image": "methylgrapher:test",
    }
    for side in ("c2t", "g2a"):
        for p in files[side].values():
            Path(p).write_bytes(b"idx")
    for key in ("ref_paths", "cpg_tsv", "linear_ref_fasta", "original_gbz"):
        Path(files[key]).write_text("x\n", encoding="utf-8")
    return {
        "c2t": {k: str(v) for k, v in files["c2t"].items()},
        "g2a": {k: str(v) for k, v in files["g2a"].items()},
        "ref_paths": str(files["ref_paths"]),
        "cpg_tsv": str(files["cpg_tsv"]),
        "linear_ref_fasta": str(files["linear_ref_fasta"]),
        "original_gbz": str(files["original_gbz"]),
        "index_prefix": files["index_prefix"],
        "directional": True,
        "image": "methylgrapher:test",
        "read_level": {"enabled": True, "tile_size": 4},
        "contexts": ["CG"],
    }


def test_step_config_defaults_are_none() -> None:
    cfg = MethylGrapherWgbsStepConfig()
    assert cfg.threads is None
    assert cfg.directional is None
    assert cfg.c2t is None
    assert cfg.align_engine is None


def test_normalize_align_engine() -> None:
    from methyl_worker.methylgrapher_wgbs_runner import _normalize_align_engine

    assert _normalize_align_engine(None) == "cpu_vg"
    assert _normalize_align_engine("gpu_giraffe") == "gpu_giraffe"
    assert _normalize_align_engine("mojo_giraffe") == "mojo_giraffe"
    with pytest.raises(RuntimeError):
        _normalize_align_engine("bam_only")


def test_align_engine_from_resolved_config_ignores_host_env(
    monkeypatch, tmp_path: Path
) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import (
        effective_align_engine,
        effective_qc_bam_engine,
        materialize_align_docker_env,
        build_align_command,
        resolve_wgbs_bundle_from_resolved,
    )

    cfg = _touch_bundle(tmp_path)
    cfg["align_engine"] = "gpu_giraffe"
    cfg["gpu_giraffe_fallback"] = "mojo"
    cfg["giraffe_device"] = "nvidia"
    cfg["mojo_giraffe_ready"] = True
    cfg["mojo_segments_cache"] = "/work/cache/mojo_segments"
    cfg["engine"] = "mojo"
    cfg["qc_bam_engine"] = "mojo"
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    monkeypatch.setenv("METHYLGRAPHER_ALIGN_ENGINE", "cpu_vg")
    monkeypatch.setenv("METHYLGRAPHER_GPU_GIRAFFE_FALLBACK", "vg")
    assert effective_align_engine(bundle) == "gpu_giraffe"
    assert effective_qc_bam_engine(bundle) == "mojo"
    from methyl_worker.methylgrapher_wgbs_runner import effective_qc_bam_fallback

    assert effective_qc_bam_fallback(bundle) == "error"
    env_pairs = materialize_align_docker_env(bundle)
    assert "METHYLGRAPHER_ALIGN_ENGINE=gpu_giraffe" in env_pairs
    assert "METHYLGRAPHER_GPU_GIRAFFE_FALLBACK=mojo" in env_pairs
    assert "MOJO_ALIGN_GIRAFFE_READY=1" in env_pairs
    assert "MOJO_ALIGN_SEGMENTS_CACHE=/work/cache/mojo_segments" in env_pairs
    assert "METHYLGRAPHER_DUAL_GRAPH_PARALLEL=0" in env_pairs
    assert "METHYLGRAPHER_GPU_REQUIRE=1" in env_pairs
    assert any(p.startswith("MODULAR_NVPTX_COMPILER_PATH=") for p in env_pairs)
    cmd = build_align_command(
        bundle=bundle,
        work_dir=tmp_path / "work",
        fq1=tmp_path / "a_R1.fastq.gz",
        fq2=tmp_path / "a_R2.fastq.gz",
        index_prefix=str(tmp_path / "hprc-d9-bs"),
    )
    assert cmd[cmd.index("-align_engine") + 1] == "gpu_giraffe"


def test_materialize_passes_host_hbm_fraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import (
        materialize_align_docker_env,
        resolve_wgbs_bundle_from_resolved,
    )

    cfg = _touch_bundle(tmp_path)
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    monkeypatch.setenv("METHYL_GPU_HBM_FREE_FRACTION", "0.90")
    env_pairs = materialize_align_docker_env(bundle)
    assert "METHYLGRAPHER_GPU_HBM_FRACTION=0.90" in env_pairs


def test_graph_handoff_floor_scales_with_device_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2T→G2A handoff floor tracks each GPU's real HBM, never a fixed GiB."""
    from methyl_worker import methylgrapher_wgbs_runner as m

    cfg = _touch_bundle(tmp_path)
    cfg["giraffe_device"] = "nvidia"
    bundle = m.resolve_wgbs_bundle_from_resolved(cfg)
    monkeypatch.setenv("METHYL_GPU_HBM_FREE_FRACTION", "0.90")
    monkeypatch.delenv("METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB", raising=False)

    monkeypatch.setattr(m, "_nvidia_hbm_free_total_gib", lambda: (8.9, 95.58))
    assert "METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB=86.02" in (
        m.materialize_align_docker_env(bundle)
    )

    # Smaller card: the floor must shrink with it, not stay at the GH200 number.
    monkeypatch.setattr(m, "_nvidia_hbm_free_total_gib", lambda: (2.0, 40.0))
    assert "METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB=36.00" in (
        m.materialize_align_docker_env(bundle)
    )

    # Unreadable HBM must not invent a floor.
    monkeypatch.setattr(m, "_nvidia_hbm_free_total_gib", lambda: None)
    assert not any(
        p.startswith("METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB=")
        for p in m.materialize_align_docker_env(bundle)
    )

    # Explicit operator pin still wins.
    monkeypatch.setattr(m, "_nvidia_hbm_free_total_gib", lambda: (8.9, 95.58))
    monkeypatch.setenv("METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB", "70")
    assert "METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB=70" in (
        m.materialize_align_docker_env(bundle)
    )


def test_orphan_gpu_matcher_covers_mojo_giraffe() -> None:
    from methyl_worker.methylgrapher_wgbs_runner import _is_orphan_gpu_container

    assert _is_orphan_gpu_container(
        "goliath/methylgrapher:1.70-mojo",
        '"methylGrapher" "MojoGiraffe" -gbz /x',
    )
    assert _is_orphan_gpu_container(
        "goliath/methylgrapher:1.70-mojo",
        '"methylGrapher" "Align" -t 64',
    )
    assert not _is_orphan_gpu_container(
        "goliath/methylgrapher:1.70-mojo",
        '"methylGrapher" "MethylCall" -t 8',
    )
    assert not _is_orphan_gpu_container("postgres:17", "Align")


def test_admission_min_free_uses_fraction(monkeypatch: pytest.MonkeyPatch) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import _admission_min_free_gib

    monkeypatch.setenv("METHYL_GPU_HBM_FREE_FRACTION", "0.90")
    monkeypatch.delenv("METHYL_GPU_ALIGN_MIN_FREE_GIB", raising=False)
    assert _admission_min_free_gib(95.577) == pytest.approx(86.0193)


def test_admission_min_free_legacy_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import _admission_min_free_gib

    monkeypatch.delenv("METHYL_GPU_HBM_FREE_FRACTION", raising=False)
    monkeypatch.setenv("METHYL_GPU_ALIGN_MIN_FREE_GIB", "90")
    assert _admission_min_free_gib(95.577) == 90.0


def test_gpu_align_lock_releases_hbm_after_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock finally must reclaim orphans even when the body raises."""
    from methyl_worker import methylgrapher_wgbs_runner as m

    monkeypatch.setenv("METHYL_GPU_HBM_FREE_FRACTION", "0.90")
    monkeypatch.setenv("METHYL_GPU_ALIGN_HBM_WAIT_S", "1")
    monkeypatch.setenv("METHYL_GPU_ALIGN_LOCK_DIR", str(tmp_path / "locks"))
    calls: list[str] = []

    def fake_kill() -> list[str]:
        calls.append("kill")
        return []

    def fake_wait(device: str, *, timeout_s: float, purpose: str) -> None:
        calls.append(f"wait:{purpose}")

    monkeypatch.setattr(m, "_kill_orphan_gpu_containers", fake_kill)
    monkeypatch.setattr(m, "_wait_host_gpu_hbm", fake_wait)

    with pytest.raises(RuntimeError, match="boom"):
        with m._gpu_align_lock("nvidia"):
            calls.append("body")
            raise RuntimeError("boom")

    assert calls == ["kill", "wait:admit", "body", "kill", "wait:release"]


def test_mojo_giraffe_ready_false_materializes_zero(tmp_path: Path) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import (
        materialize_align_docker_env,
        resolve_wgbs_bundle_from_resolved,
    )

    cfg = _touch_bundle(tmp_path)
    cfg["mojo_giraffe_ready"] = False
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    assert "MOJO_ALIGN_GIRAFFE_READY=0" in materialize_align_docker_env(bundle)


def test_mojo_segment_cache_mounts_always_added_for_qc_reuse(tmp_path: Path) -> None:
    """GAF reuse must still mount segment pack cache for QC MojoGiraffe."""
    from methyl_worker.methylgrapher_wgbs_runner import (
        add_mojo_segment_cache_mounts,
        mojo_segments_cache_path,
        resolve_wgbs_bundle_from_resolved,
    )

    cfg = _touch_bundle(tmp_path)
    cfg["mojo_segments_cache"] = str(tmp_path / "mojo_segments")
    (tmp_path / "mojo_segments").mkdir()
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    roots: set = {tmp_path / "sample"}
    add_mojo_segment_cache_mounts(roots, bundle)
    assert mojo_segments_cache_path(bundle).resolve() in roots


def test_qc_bam_fallback_error_default_and_vg_opt_in(tmp_path: Path) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import (
        effective_qc_bam_fallback,
        materialize_align_docker_env,
        resolve_wgbs_bundle_from_resolved,
    )

    cfg = _touch_bundle(tmp_path)
    cfg["engine"] = "mojo"
    cfg["qc_bam_engine"] = "mojo"
    cfg["giraffe_device"] = "nvidia"
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    assert effective_qc_bam_fallback(bundle) == "error"
    assert "METHYLGRAPHER_DUAL_GRAPH_PARALLEL=0" in materialize_align_docker_env(bundle)

    cfg["qc_bam_fallback"] = "vg"
    cfg["dual_graph_parallel"] = True
    bundle2 = resolve_wgbs_bundle_from_resolved(cfg)
    assert effective_qc_bam_fallback(bundle2) == "vg"
    assert "METHYLGRAPHER_DUAL_GRAPH_PARALLEL=1" in materialize_align_docker_env(bundle2)


def test_resolve_giraffe_device_and_gpu_flags(tmp_path: Path, monkeypatch) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import (
        align_docker_gpu_flags,
        materialize_align_docker_env,
        resolve_giraffe_device,
        resolve_wgbs_bundle_from_resolved,
    )

    cfg = _touch_bundle(tmp_path)
    cfg["giraffe_device"] = "nvidia"
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    assert resolve_giraffe_device(bundle) == "nvidia"
    assert align_docker_gpu_flags("nvidia") == ["--gpus", "all"]
    assert align_docker_gpu_flags("cpu") == []
    assert "METHYLGRAPHER_GIRAFFE_DEVICE=nvidia" in materialize_align_docker_env(bundle)

    cfg["giraffe_device"] = "auto"
    bundle_auto = resolve_wgbs_bundle_from_resolved(cfg)
    monkeypatch.setattr(
        "methyl_worker.methylgrapher_wgbs_runner.shutil.which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
    monkeypatch.setattr(
        "methyl_worker.methylgrapher_wgbs_runner.subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    assert resolve_giraffe_device(bundle_auto) == "nvidia"
    # Container env must be concrete (not auto) after host probe.
    assert "METHYLGRAPHER_GIRAFFE_DEVICE=nvidia" in materialize_align_docker_env(
        bundle_auto
    )


def test_resolve_bundle_requires_c2t_g2a(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing"):
        resolve_methylgrapher_wgbs_genome(resolved_config={"ref_paths": str(tmp_path / "x")})


def test_resolve_bundle_ok_and_no_stock_fallback(tmp_path: Path) -> None:
    cfg = _touch_bundle(tmp_path)
    resolved = resolve_methylgrapher_wgbs_genome(resolved_config=cfg)
    assert resolved["c2t"]["gbz"].endswith("c2t.gbz")
    assert resolved["g2a"]["gbz"].endswith("g2a.gbz")
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    assert bundle.directional is True
    assert bundle.c2t_gbz.is_file()


def test_mojo_qc_sam_provenance_gates_giraffe_reuse(tmp_path: Path) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import (
        giraffe_bam_reusable_for_mojo_qc,
        write_mojo_qc_sam_provenance,
    )

    qc_bam = tmp_path / "sample.giraffe.bam"
    qc_bam.write_bytes(b"BAM\x01")
    assert giraffe_bam_reusable_for_mojo_qc(qc_bam) is False
    write_mojo_qc_sam_provenance(qc_bam, offsets_dir=tmp_path / "offsets")
    assert giraffe_bam_reusable_for_mojo_qc(qc_bam) is True


def test_build_qc_bam_mojo_sam_command_uses_out_sam(tmp_path: Path) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import build_qc_bam_mojo_sam_command

    cfg = _touch_bundle(tmp_path)
    cfg["engine"] = "mojo"
    cfg["qc_bam_engine"] = "mojo"
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    out_sam = tmp_path / "qc.sam"
    offsets = tmp_path / "offsets"
    offsets.mkdir()
    cmd = build_qc_bam_mojo_sam_command(
        bundle=bundle,
        fq1_c2t=tmp_path / "a.C2T.R1.fastq",
        fq2_g2a=tmp_path / "a.G2A.R2.fastq",
        out_sam=out_sam,
        segment_offsets=offsets,
    )
    assert "-out_sam" in cmd
    assert cmd[cmd.index("-out_sam") + 1] == str(out_sam)
    assert "-segment_offsets" in cmd
    assert "-out_gaf" not in cmd


def test_build_align_and_qc_bam_commands(tmp_path: Path) -> None:
    cfg = _touch_bundle(tmp_path)
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    cmd = build_align_command(
        bundle=bundle,
        work_dir=tmp_path / "work",
        fq1=tmp_path / "a_R1.fastq.gz",
        fq2=tmp_path / "a_R2.fastq.gz",
        index_prefix=str(tmp_path / "hprc-d9-bs"),
    )
    assert cmd[0].endswith("methylGrapher") or cmd[0] == "methylGrapher"
    assert "Align" in cmd
    assert "-directional" in cmd
    assert "-align_engine" in cmd
    assert cmd[cmd.index("-align_engine") + 1] == "cpu_vg"
    qc = build_qc_bam_command(
        bundle=bundle,
        fq1_c2t=tmp_path / "a.C2T.R1.fastq",
        fq2_g2a=tmp_path / "a.G2A.R2.fastq",
        threads=8,
    )
    assert qc[0] == "vg" or qc[0].endswith("vg")
    assert "giraffe" in qc
    assert qc[qc.index("-o") + 1] == "BAM"  # surjection happens inside giraffe
    assert qc[qc.index("--ref-paths") + 1] == str(bundle.ref_paths)
    assert qc[qc.index("-Z") + 1] == str(bundle.c2t_gbz)
    assert [qc[i + 1] for i, a in enumerate(qc) if a == "-f"] == [
        str(tmp_path / "a.C2T.R1.fastq"),
        str(tmp_path / "a.G2A.R2.fastq"),
    ]
    # Named coordinates are why the GAF cannot be surjected; never ask for them.
    assert "--named-coordinates" not in qc
    assert "surject" not in qc


def test_bs_conversion_rewrites_only_sequence_lines(tmp_path: Path) -> None:
    src = tmp_path / "r1.fastq"
    src.write_text(
        "@read1\nACGTCC\n+\nIIICCI\n@read2\nCCCCGG\n+\nCCIIII\n",
        encoding="utf-8",
    )
    out = tmp_path / "r1.C2T.fastq"
    _write_bs_converted_fastq(
        src, out, base_from="C", base_to="T", log_path=tmp_path / "log.txt"
    )
    assert out.read_text(encoding="utf-8") == (
        "@read1\nATGTTT\n+\nIIICCI\n@read2\nTTTTGG\n+\nCCIIII\n"
    )


def test_run_binary_stdout_redirect(tmp_path: Path) -> None:
    out = tmp_path / "blob.bin"
    log = tmp_path / "run.log"
    payload = b"\x1f\x8bBAM\x00\x01\x02"
    src = tmp_path / "src.bin"
    src.write_bytes(payload)
    _run(["cat", str(src)], log, step="bin", stdout_path=out)
    assert out.read_bytes() == payload


@pytest.mark.skipif(not _HAS_SAMTOOLS, reason="samtools not on PATH")
def test_restore_sequences_streaming_not_dict_load(tmp_path: Path) -> None:
    """Restore must merge-join with matching lex collation (incl. numeric names)."""
    import pysam

    bam_in = tmp_path / "conv.bam"
    header = {
        "HD": {"VN": "1.0", "SO": "unsorted"},
        "SQ": [{"LN": 1000, "SN": "chr1"}],
    }
    # Numeric suffixes diverge under natural (-n) vs lex (-N / unix sort):
    # natural: read9 < read10 ; lex: read10 < read9.
    with pysam.AlignmentFile(str(bam_in), "wb", header=header) as out:
        for qname, seq, flag, start in (
            ("read9", "AAAA", 99, 5),
            ("read10", "CCCC", 99, 10),
            ("read10", "GGGG", 147, 20),
            ("read9", "TTTT", 147, 30),
            ("read2", "ATAT", 99, 40),
            ("read2", "GCGC", 147, 50),
        ):
            aln = pysam.AlignedSegment()
            aln.query_name = qname
            aln.query_sequence = seq
            aln.flag = flag
            aln.reference_id = 0
            aln.reference_start = start
            aln.mapping_quality = 60
            aln.cigarstring = f"{len(seq)}M"
            aln.query_qualities = pysam.qualitystring_to_array("I" * len(seq))
            out.write(aln)

    fq1 = tmp_path / "r1.fastq"
    fq2 = tmp_path / "r2.fastq"
    fq1.write_text(
        "@read9\nACGT\n+\nIIII\n"
        "@read2\nTGCA\n+\nIIII\n"
        "@read10\nAATT\n+\nIIII\n",
        encoding="utf-8",
    )
    fq2.write_text(
        "@read10\nTTAA\n+\nIIII\n"
        "@read9\nGGCC\n+\nIIII\n"
        "@read2\nCCGG\n+\nIIII\n",
        encoding="utf-8",
    )
    out_bam = tmp_path / "restored.bam"
    log = tmp_path / "restore.log"
    _restore_original_sequences(bam_in, fq1, fq2, out_bam, log)
    assert out_bam.is_file()
    by_name = {}
    with pysam.AlignmentFile(str(out_bam), "rb") as inn:
        for aln in inn:
            by_name[(aln.query_name, aln.is_read2)] = aln.query_sequence
    assert by_name[("read2", False)] == "TGCA"
    assert by_name[("read2", True)] == "CCGG"
    assert by_name[("read9", False)] == "ACGT"
    assert by_name[("read9", True)] == "GGCC"
    assert by_name[("read10", False)] == "AATT"
    assert by_name[("read10", True)] == "TTAA"
    assert "streaming merge-join" in log.read_text(encoding="utf-8")


def test_dry_run_align_and_extract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METHYL_METHYLGRAPHER_DRY_RUN", "1")
    cfg = _touch_bundle(tmp_path / "assets")
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    (sample_dir / "S1_R1.fastq.gz").write_bytes(b"\x1f\x8b")  # tiny gzip-ish
    (sample_dir / "S1_R2.fastq.gz").write_bytes(b"\x1f\x8b")

    # resolve_paired_fastqs may need real pair naming — ensure helper accepts these.
    from methyl_worker import parabricks_runner

    monkeypatch.setattr(
        parabricks_runner,
        "resolve_paired_fastqs",
        lambda sample_path, sample_id: (
            sample_path / f"{sample_id}_R1.fastq.gz",
            sample_path / f"{sample_id}_R2.fastq.gz",
        ),
    )

    align = run_methylgrapher_wgbs_align(
        sample_id="S1",
        sample_dir=sample_dir,
        input_json={"resolvedConfig": cfg},
    )
    assert Path(align["bamPath"]).is_file()
    assert Path(align["gafPath"]).is_file()
    assert Path(align["qcMetricsTar"]).is_file()
    assert Path(align["dedupMetricsPath"]).is_file()

    extract = run_methylgrapher_wgbs_extract(
        sample_id="S1",
        sample_dir=sample_dir,
        project=str(tmp_path / "project.json"),
        input_json={"resolvedConfig": cfg, "forceRealign": True},
    )
    assert extract["n_h5_files"] >= 1
    h5 = Path(extract["h5Files"][0])
    assert h5.is_file()
    assert h5.name.endswith("-CG.h5")
    patterns = list(sample_dir.glob("*.patterns.h5"))
    assert patterns

    manifest_path = Path(extract["manifestPath"])
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["metadata"]["schema_name"] == "methylextractor.extraction_manifest"
    assert manifest["metadata"]["contexts_extracted"] == ["CG"]
    assert manifest["metadata"]["extractor"] == "methylGrapher"
    assert "cpg_weighted_mean_coverage" in manifest["summary"]
    assert manifest["summary"]["cpg_weighted_mean_coverage"] >= 10.0
    assert "1" in manifest["per_chromosome"]
    assert isinstance(manifest["per_chromosome"]["1"].get("CG"), dict)
    assert manifest["per_chromosome"]["1"]["CG"]["num_positions"] >= 1
    assert "read_filtering" not in manifest  # unavailable for graph extract; skip check
    assert manifest["tool"] == "methylGrapher"
    assert "graph_assets" in manifest

    from methyl_extraction_qc.guardrails import evaluate_guardrails
    from methyl_extraction_qc.models.config import ExtractionQCGuardrailConfig

    report = evaluate_guardrails(
        manifest,
        config=ExtractionQCGuardrailConfig(),
        expected_chromosomes=["1"],
    )
    assert report["overall_pass"] is True
    assert report["metrics"]["cpg_weighted_mean_coverage"]["pass"] is True
    assert report["metrics"]["chromosome_completeness"]["pass"] is True
    assert report["metrics"]["read_discard_fraction"]["pass"] is True


def test_force_realign_clears_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METHYL_METHYLGRAPHER_DRY_RUN", "1")
    cfg = _touch_bundle(tmp_path / "assets")
    sample_dir = tmp_path / "S2"
    sample_dir.mkdir()
    from methyl_worker import parabricks_runner

    monkeypatch.setattr(
        parabricks_runner,
        "resolve_paired_fastqs",
        lambda sample_path, sample_id: (
            sample_path / f"{sample_id}_R1.fastq.gz",
            sample_path / f"{sample_id}_R2.fastq.gz",
        ),
    )
    (sample_dir / "S2_R1.fastq.gz").write_bytes(b"x")
    (sample_dir / "S2_R2.fastq.gz").write_bytes(b"x")
    first = run_methylgrapher_wgbs_align(
        sample_id="S2", sample_dir=sample_dir, input_json={"resolvedConfig": cfg}
    )
    gaf = Path(first["gafPath"])
    stamp = gaf.read_text(encoding="utf-8")
    gaf.write_text("stale\n", encoding="utf-8")
    second = run_methylgrapher_wgbs_align(
        sample_id="S2",
        sample_dir=sample_dir,
        input_json={"resolvedConfig": cfg, "forceRealign": True},
    )
    assert Path(second["gafPath"]).read_text(encoding="utf-8") != "stale\n"
    assert stamp  # dry-run marker existed



def test_align_resolves_index_prefix_for_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Config may store paths via a symlink (/work); Docker mounts use realpath."""
    assets = tmp_path / "assets"
    cfg = _touch_bundle(assets)
    link_root = tmp_path / "work_link"
    link_root.symlink_to(assets.resolve(), target_is_directory=True)
    cfg = {**cfg, "index_prefix": str(link_root / "hprc-d9-bs")}

    sample_dir = tmp_path / "S_idx"
    sample_dir.mkdir()
    (sample_dir / "S_idx_R1.fastq.gz").write_bytes(b"x")
    (sample_dir / "S_idx_R2.fastq.gz").write_bytes(b"x")
    from methyl_worker import parabricks_runner

    monkeypatch.setattr(
        parabricks_runner,
        "resolve_paired_fastqs",
        lambda sample_path, sample_id: (
            sample_path / f"{sample_id}_R1.fastq.gz",
            sample_path / f"{sample_id}_R2.fastq.gz",
        ),
    )
    captured: dict[str, str] = {}
    real_build = build_align_command

    def wrap_build(**kwargs):
        captured["index_prefix"] = kwargs["index_prefix"]
        return real_build(**kwargs)

    monkeypatch.setattr(
        "methyl_worker.methylgrapher_wgbs_runner.build_align_command",
        wrap_build,
    )
    monkeypatch.setenv("METHYL_METHYLGRAPHER_DRY_RUN", "1")

    run_methylgrapher_wgbs_align(
        sample_id="S_idx",
        sample_dir=sample_dir,
        input_json={"resolvedConfig": cfg},
    )
    assert captured["index_prefix"] == str((assets / "hprc-d9-bs").resolve())
    assert "work_link" not in captured["index_prefix"]


def test_task_models_accept_resolved_config() -> None:
    align = MethylGrapherWgbsAlignTaskInput(
        sampleId="S1",
        sampleDir="/work/samples/S1",
        resolvedConfig={"directional": True},
        forceRealign=True,
    )
    assert align.tool == "MethylGrapherWgbsAlign"
    extract = MethylGrapherWgbsExtractTaskInput(
        sampleId="S1",
        sampleDir="/work/samples/S1",
        projectPath="/work/projects/x/configs/project.json",
        resolvedConfig={"contexts": ["CG"]},
    )
    assert extract.projectPath.endswith("project.json")


def test_sample_prep_program_has_three_way_branch() -> None:
    root = Path(__file__).resolve().parents[2]
    prog = json.loads(
        (root / "workflow_engine/domain/fixtures/sample_prep.program.json").read_text()
    )
    text = json.dumps(prog)
    assert "${useWgbsPangenome}" in text
    assert "sample.methylgrapher_wgbs_align" in text
    assert "sample.methylgrapher_wgbs_extract" in text
    assert "sample.parabricks_giraffe" in text
    assert "sample.parabricks_fq2bam" in text


def test_methylcall_preflight_requires_wl_gfa(tmp_path: Path) -> None:
    cfg = _touch_bundle(tmp_path)
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    index_prefix = str(tmp_path / "hprc-d9-bs")
    with pytest.raises(RuntimeError, match="wl.gfa"):
        assert_methylcall_assets(bundle, index_prefix)
    wl = Path(f"{index_prefix}.wl.gfa")
    wl.write_text("H\tVN:Z:1.1\n", encoding="utf-8")
    nr = Path(f"{index_prefix}.wl.node.replacement.json")
    nr.write_text('{"CT":{},"GA":{}}\n', encoding="utf-8")
    assert assert_methylcall_assets(bundle, index_prefix) == wl


def test_build_methylcall_clamps_threads_and_batch_size(tmp_path: Path) -> None:
    cfg = {**_touch_bundle(tmp_path), "threads": 64, "batch_size": 8192}
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    cmd = build_methylcall_command(
        bundle=bundle, work_dir=tmp_path / "work", index_prefix=str(tmp_path / "hprc-d9-bs")
    )
    assert cmd[cmd.index("-t") + 1] == "16"
    assert cmd[cmd.index("-batch_size") + 1] == "8192"
    assert "MethylCall" in cmd


def test_mojo_engine_skips_thread_cap_and_defaults_image(tmp_path: Path) -> None:
    from methyl_worker.methylgrapher_wgbs_runner import _resolve_image

    cfg = {**_touch_bundle(tmp_path), "threads": 64, "engine": "mojo"}
    del cfg["image"]
    bundle = resolve_wgbs_bundle_from_resolved(cfg)
    assert bundle.engine == "mojo"
    cmd = build_methylcall_command(
        bundle=bundle, work_dir=tmp_path / "work", index_prefix=str(tmp_path / "hprc-d9-bs")
    )
    assert cmd[cmd.index("-t") + 1] == "64"
    assert _resolve_image(bundle) == "goliath/methylgrapher:1.70-mojo"


def test_step_config_accepts_engine_mojo() -> None:
    cfg = MethylGrapherWgbsStepConfig(engine="mojo")
    assert cfg.engine == "mojo"


def test_project_graph_cpg_to_linear_tsv(tmp_path: Path) -> None:
    gfa = tmp_path / "toy.wl.gfa"
    # Segment 11 starts at path offset 1000 on GRCh38 chr1.
    gfa.write_text(
        "\n".join(
            [
                "H\tVN:Z:1.1",
                "S\t11\tACGTCGGA",
                "S\t12\tTTCGAA",
                "W\tGRCh38\t0\tchr1\t1000\t1014\t>11>12",
                "",
            ]
        ),
        encoding="utf-8",
    )
    offsets = build_grch38_segment_offsets_from_gfa(gfa)
    assert offsets["11"] == ("1", 1000, 8, ">")
    assert offsets["12"] == ("1", 1008, 6, ">")

    cpg_reg = tmp_path / "cpg.tsv"
    # C0 at seg11 pos 1 (0-based; seq ACGTCGGA → CG at index 1) → 1-based genomic 1002
    cpg_reg.write_text("C0\t11\t1\t11\t2\tOther\n", encoding="utf-8")
    graph_cpg = tmp_path / "graph.cpg.tsv"
    graph_cpg.write_text("C0\t3\t10\n", encoding="utf-8")
    out = tmp_path / "linear.tsv"
    n = project_graph_cpg_to_linear_tsv(
        graph_cpg_tsv=graph_cpg,
        cpg_registry_tsv=cpg_reg,
        segment_offsets=offsets,
        out_tsv=out,
    )
    assert n == 1
    rows = out.read_text(encoding="utf-8").strip().splitlines()
    assert rows[0] == "chrom\tpos\tmC\tuC\ttnc"
    assert rows[1] == "1\t1002\t3\t7\t1"


def test_project_reverse_oriented_segment(tmp_path: Path) -> None:
    gfa = tmp_path / "toy.wl.gfa"
    # Reverse walk: segment length 4 at path start 1000 → pos0=1 maps to 1-based 1003
    # (0-based genomic = 1000 + (4-1-1) = 1002 → 1-based 1003)
    gfa.write_text(
        "\n".join(
            [
                "H\tVN:Z:1.1",
                "S\t9\tACGT",
                "W\tGRCh38\t0\tchr2\t1000\t1004\t<9",
                "",
            ]
        ),
        encoding="utf-8",
    )
    offsets = build_grch38_segment_offsets_from_gfa(gfa)
    assert offsets["9"] == ("2", 1000, 4, "<")
    cpg_reg = tmp_path / "cpg.tsv"
    cpg_reg.write_text("C0\t9\t1\t9\t2\tOther\n", encoding="utf-8")
    graph_cpg = tmp_path / "graph.cpg.tsv"
    graph_cpg.write_text("C0\t1\t4\n", encoding="utf-8")
    out = tmp_path / "linear.tsv"
    n = project_graph_cpg_to_linear_tsv(
        graph_cpg_tsv=graph_cpg,
        cpg_registry_tsv=cpg_reg,
        segment_offsets=offsets,
        out_tsv=out,
    )
    assert n == 1
    assert out.read_text(encoding="utf-8").strip().splitlines()[1] == "2\t1003\t1\t3\t1"


def test_segment_offsets_ignore_non_reference_walks(tmp_path: Path) -> None:
    gfa = tmp_path / "toy.wl.gfa"
    # Segments 21/23 are only reachable from non-GRCh38 haplotypes, and the
    # sample haplotypes visit shared segment 22 at a different offset.
    gfa.write_text(
        "\n".join(
            [
                "H\tVN:Z:1.1",
                "S\t21\tACGT",
                "S\t22\tCGCG",
                "S\t23\tTTAA",
                "W\tHG002\t1\tchr1\t500\t508\t>21>22",
                "W\tCHM13\t0\tchr1\t700\t708\t>22>23",
                "W\tGRCh38\t0\tchr1\t1000\t1004\t>22",
                "",
            ]
        ),
        encoding="utf-8",
    )
    offsets = build_grch38_segment_offsets_from_gfa(gfa)
    assert offsets == {"22": ("1", 1000, 4, ">")}


def test_catalog_registers_methylgrapher_actions() -> None:
    from methyl_worker.action_catalog import ACTION_CATALOG

    names = {e.action_name for e in ACTION_CATALOG}
    assert "sample.methylgrapher_wgbs_align" in names
    assert "sample.methylgrapher_wgbs_extract" in names
    entries = {e.action_name: e for e in ACTION_CATALOG}
    assert entries["sample.methylgrapher_wgbs_align"].action_config_key == "methylgrapher_wgbs"
    assert entries["sample.methylgrapher_wgbs_extract"].in_process_handler == (
        "_handle_methylgrapher_wgbs_extract"
    )


@pytest.mark.skipif(not _HAS_SAMTOOLS, reason="samtools not on PATH")
def test_ensure_qc_bam_read_group_stamps_lb_like_linear_giraffe(tmp_path: Path) -> None:
    """Clara collectmultiplemetrics needs @RG LB=library (same as pbrun giraffe)."""
    import subprocess

    sample_id = "DBCST-RGTEST"
    bam = tmp_path / f"{sample_id}.bam"
    unsorted = tmp_path / "unsorted.bam"
    log = tmp_path / "rg.log"
    sam = tmp_path / "tiny.sam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:unsorted\n"
        "@SQ\tSN:1\tLN:1000\n"
        f"{sample_id}\t0\t1\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["samtools", "view", "-b", "-o", str(unsorted), str(sam)],
        check=True,
        capture_output=True,
    )
    # Coordinate-sorted BGZF BAM (matches post-markdup QC BAMs).
    subprocess.run(
        ["samtools", "sort", "-o", str(bam), str(unsorted)],
        check=True,
        capture_output=True,
    )
    assert ensure_qc_bam_read_group(bam, sample_id, log) is True
    header = subprocess.check_output(["samtools", "view", "-H", str(bam)], text=True)
    assert "@RG" in header
    assert "LB:library" in header
    assert f"SM:{sample_id}" in header
    # Idempotent when LB already present.
    assert ensure_qc_bam_read_group(bam, sample_id, log) is False


def test_share_work_path_opens_mode_600_for_fleet_uids(tmp_path: Path) -> None:
    f = tmp_path / "alignment.gaf"
    f.write_text("x\n", encoding="utf-8")
    f.chmod(0o600)
    share_work_path(f)
    assert f.stat().st_mode & 0o004  # other-read
    assert f.stat().st_mode & 0o002  # other-write (fleet co-writers)


def test_flatten_and_package_nested_picard_metrics(tmp_path: Path) -> None:
    """Nested Parabricks outputs must all land in the flat qc-metrics tar."""
    import tarfile

    sample_id = "HBCST-NEST"
    sample_dir = tmp_path / sample_id
    metrics_dir = sample_dir / f"{sample_id}.qc-metrics"
    nested = metrics_dir / "collectmultiplemetrics"
    nested.mkdir(parents=True)
    # quality_yield already at root (old bug skipped flatten entirely in this case)
    (metrics_dir / "quality_yield.txt").write_text("TOTAL_READS\t1000\n", encoding="utf-8")
    for name in (
        "insert_size.txt",
        "gcbias_summary.txt",
        "mean_quality_by_cycle.txt",
        "sequencingArtifact.pre_adapter_summary_metrics.txt",
    ):
        (nested / name).write_text(f"{name}\tok\n", encoding="utf-8")

    _flatten_qc_metrics_dir(metrics_dir)
    for name in (
        "quality_yield.txt",
        "insert_size.txt",
        "gcbias_summary.txt",
        "mean_quality_by_cycle.txt",
        "sequencingArtifact.pre_adapter_summary_metrics.txt",
    ):
        assert (metrics_dir / name).is_file(), name

    tar_path = _package_qc_tar(sample_dir, sample_id, metrics_dir)
    with tarfile.open(tar_path, "r") as tar:
        names = set(tar.getnames())
    assert "quality_yield.txt" in names
    assert "insert_size.txt" in names
    assert "gcbias_summary.txt" in names
    assert "mean_quality_by_cycle.txt" in names
    assert "sequencingArtifact.pre_adapter_summary_metrics.txt" in names


def test_package_qc_tar_includes_nested_only_metrics(tmp_path: Path) -> None:
    """Packaging safety net: nested-only trees still enter the tar by basename."""
    import tarfile

    sample_id = "HBCST-NEST2"
    sample_dir = tmp_path / sample_id
    metrics_dir = sample_dir / f"{sample_id}.qc-metrics"
    nested = metrics_dir / "out"
    nested.mkdir(parents=True)
    (nested / "quality_yield.txt").write_text("TOTAL_READS\t1\n", encoding="utf-8")
    (nested / "insert_size.txt").write_text("MEDIAN_INSERT_SIZE\t180\n", encoding="utf-8")

    tar_path = _package_qc_tar(sample_dir, sample_id, metrics_dir)
    with tarfile.open(tar_path, "r") as tar:
        names = set(tar.getnames())
    assert names == {"quality_yield.txt", "insert_size.txt"}


def test_package_qc_tar_prefers_root_over_nested_same_basename(tmp_path: Path) -> None:
    """Root-level metric wins when a nested duplicate sorts earlier by path."""
    import tarfile

    sample_id = "HBCST-ROOTWIN"
    sample_dir = tmp_path / sample_id
    metrics_dir = sample_dir / f"{sample_id}.qc-metrics"
    # Subdir name 'collectmultiplemetrics' sorts before filename under old key.
    nested = metrics_dir / "collectmultiplemetrics"
    nested.mkdir(parents=True)
    root_qy = "TOTAL_READS\tROOT\n"
    nested_qy = "TOTAL_READS\tNESTED\n"
    (metrics_dir / "quality_yield.txt").write_text(root_qy, encoding="utf-8")
    (nested / "quality_yield.txt").write_text(nested_qy, encoding="utf-8")
    (nested / "insert_size.txt").write_text("MEDIAN_INSERT_SIZE\t180\n", encoding="utf-8")

    tar_path = _package_qc_tar(sample_dir, sample_id, metrics_dir)
    with tarfile.open(tar_path, "r") as tar:
        names = set(tar.getnames())
        qy = tar.extractfile("quality_yield.txt")
        assert qy is not None
        assert qy.read().decode("utf-8") == root_qy
    assert names == {"quality_yield.txt", "insert_size.txt"}
