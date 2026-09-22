"""Tests for MethylExtractor read_level wiring."""

from __future__ import annotations

import json

from methyl_worker.extract_runner import (
    MethylExtractConfig,
    _resolve_mhap,
    _resolve_read_level,
    build_methyl_extractor_command,
    expected_pattern_h5_files,
)


def test_resolve_read_level_dict():
    enabled, tile = _resolve_read_level({"read_level": {"enabled": True, "tile_size": 6}})
    assert enabled is True
    assert tile == 6


def test_resolve_mhap_dict_and_bool():
    assert _resolve_mhap({"mhap": {"enabled": True}}) is True
    assert _resolve_mhap({"mhap": {"enabled": False}}) is False
    assert _resolve_mhap({"mhap": True}) is True
    assert _resolve_mhap({}) is False


def test_build_command_includes_read_level_flags(tmp_path):
    fake_bin = tmp_path / "MethylExtractor.exe"
    # Executable stub: --help must document --read-level so the probe succeeds.
    fake_bin.write_text(
        "#!/bin/sh\necho 'Usage: MethylExtractor --read-level --tile-size=N'\n",
        encoding="utf-8",
    )
    fake_bin.chmod(0o755)
    cfg = MethylExtractConfig(
        sample_id="s1",
        sample_dir=tmp_path,
        project_path=tmp_path / "p.json",
        chromosomes=("1",),
        extract_contexts=("CG",),
        reference_fasta=tmp_path / "ref.fa",
        chrom_mapping=tmp_path / "map.json",
        extractor_bin=str(fake_bin),
        threads=None,
        min_mapq=None,
        min_phred=None,
        min_cov=None,
        cap_cov=None,
        compression=None,
        chunk_size=None,
        output_format="hdf5",
        split=True,
        read_level=True,
        tile_size=4,
    )
    from methyl_worker.extract_runner import MethylExtractPaths

    paths = MethylExtractPaths(
        sample_dir=tmp_path,
        sample_id="s1",
        bam_path=tmp_path / "s1.bam",
        log_path=tmp_path / "s1.log",
    )
    cmd = build_methyl_extractor_command(cfg, paths)
    assert "--read-level" in cmd
    assert "--tile-size=4" in cmd


def test_build_command_includes_mhap_when_supported(tmp_path, monkeypatch):
    fake_bin = tmp_path / "MethylExtractor.exe"
    fake_bin.write_text("stub\n", encoding="utf-8")
    from methyl_worker import extract_runner as er

    monkeypatch.setattr(er, "_extractor_supports_flag", lambda _bin, flag: flag == "--mhap")
    cfg = MethylExtractConfig(
        sample_id="s1",
        sample_dir=tmp_path,
        project_path=tmp_path / "p.json",
        chromosomes=("1",),
        extract_contexts=("CG",),
        reference_fasta=tmp_path / "ref.fa",
        chrom_mapping=tmp_path / "map.json",
        extractor_bin=str(fake_bin),
        threads=None,
        min_mapq=None,
        min_phred=None,
        min_cov=None,
        cap_cov=None,
        compression=None,
        chunk_size=None,
        output_format="hdf5",
        split=True,
        read_level=False,
        tile_size=None,
        mhap=True,
    )
    paths = er.MethylExtractPaths(
        sample_dir=tmp_path,
        sample_id="s1",
        bam_path=tmp_path / "s1.bam",
        log_path=tmp_path / "s1.log",
    )
    cmd = er.build_methyl_extractor_command(cfg, paths)
    assert "--mhap" in cmd


def test_build_command_omits_read_level_when_unsupported(tmp_path):
    fake_bin = tmp_path / "MethylExtractor.exe"
    fake_bin.write_text(
        "#!/bin/sh\necho 'Usage: MethylExtractor --split'\n",
        encoding="utf-8",
    )
    fake_bin.chmod(0o755)
    cfg = MethylExtractConfig(
        sample_id="s1",
        sample_dir=tmp_path,
        project_path=tmp_path / "p.json",
        chromosomes=("1",),
        extract_contexts=("CG",),
        reference_fasta=tmp_path / "ref.fa",
        chrom_mapping=tmp_path / "map.json",
        extractor_bin=str(fake_bin),
        threads=None,
        min_mapq=None,
        min_phred=None,
        min_cov=None,
        cap_cov=None,
        compression=None,
        chunk_size=None,
        output_format="hdf5",
        split=True,
        read_level=True,
        tile_size=4,
    )
    from methyl_worker.extract_runner import MethylExtractPaths

    paths = MethylExtractPaths(
        sample_dir=tmp_path,
        sample_id="s1",
        bam_path=tmp_path / "s1.bam",
        log_path=tmp_path / "s1.log",
    )
    cmd = build_methyl_extractor_command(cfg, paths)
    assert "--read-level" not in cmd
    assert "--tile-size=4" not in cmd


def test_expected_pattern_files():
    assert expected_pattern_h5_files(["1", "2"], ["CG"]) == [
        "1-CG.patterns.h5",
        "2-CG.patterns.h5",
    ]


def test_skip_when_patterns_missing_without_bam(tmp_path, monkeypatch):
    """Marginals present + read_level on + no BAM → soft skip (no fail)."""
    from methyl_worker import extract_runner as er

    sample_id = "s1"
    sample_dir = tmp_path / sample_id
    sample_dir.mkdir()
    (sample_dir / "1-CG.h5").write_text("x", encoding="utf-8")
    # MethylExtractor writes a per-chromosome stats sidecar next to each HDF5;
    # the canonical extraction manifest is built from those.
    (sample_dir / "1-CG.json").write_text(
        json.dumps(
            {
                "num_positions": 100,
                "total_methylated": 80,
                "total_unmethylated": 20,
                "avg_methylation_level": 0.8,
                "avg_coverage": 12.0,
            }
        ),
        encoding="utf-8",
    )
    project = tmp_path / "project.json"
    project.write_text("{}", encoding="utf-8")

    cfg = er.MethylExtractConfig(
        sample_id=sample_id,
        sample_dir=sample_dir,
        project_path=project,
        chromosomes=("1",),
        extract_contexts=("CG",),
        reference_fasta=tmp_path / "ref.fa",
        chrom_mapping=tmp_path / "map.json",
        extractor_bin="MethylExtractor",
        threads=None,
        min_mapq=None,
        min_phred=None,
        min_cov=None,
        cap_cov=None,
        compression=None,
        chunk_size=None,
        output_format="hdf5",
        split=True,
        read_level=True,
        tile_size=4,
    )
    monkeypatch.setattr(er, "resolve_methyl_extract_config", lambda *a, **k: cfg)
    out = er.run_methyl_extract(
        sample_id=sample_id,
        sample_dir=sample_dir,
        project=project,
    )
    assert out["sampleId"] == sample_id
    assert out.get("patternsIncomplete") is True

