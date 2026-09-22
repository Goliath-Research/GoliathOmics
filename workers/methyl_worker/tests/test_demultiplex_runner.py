"""Unit tests for barcode demultiplex runner."""

from __future__ import annotations

import gzip
from pathlib import Path

from methyl_worker.demultiplex_runner import run_demultiplex


def _write_fastq(path: Path, records: list[tuple[str, str, str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for name, seq, qual in records:
            fh.write(f"@{name}\n{seq}\n+\n{qual}\n")


def test_demultiplex_keeps_matching_barcode(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    _write_fastq(
        sample_dir / "S1_R1.fastq.gz",
        [
            ("r1", "ACGTAAAA", "IIIIIIII"),
            ("r2", "TTTTAAAA", "IIIIIIII"),
        ],
    )
    _write_fastq(
        sample_dir / "S1_R2.fastq.gz",
        [
            ("r1", "GGGGGGGG", "IIIIIIII"),
            ("r2", "CCCCCCCC", "IIIIIIII"),
        ],
    )
    barcodes = tmp_path / "barcodes.tsv"
    barcodes.write_text("sample_id\tbarcode\nS1\tACGT\n", encoding="utf-8")
    result = run_demultiplex(
        sample_id="S1",
        sample_dir=sample_dir,
        input_json={"resolvedConfig": {"barcode_tsv": str(barcodes)}},
    )
    assert result["skipped"] is False
    assert result["n_reads_kept"] == 1
    assert Path(result["fastqR1"]).is_file()


def test_demultiplex_skip_flag(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S2"
    sample_dir.mkdir()
    _write_fastq(sample_dir / "S2_R1.fastq.gz", [("r1", "ACGT", "IIII")])
    _write_fastq(sample_dir / "S2_R2.fastq.gz", [("r1", "TTTT", "IIII")])
    result = run_demultiplex(
        sample_id="S2",
        sample_dir=sample_dir,
        input_json={"resolvedConfig": {"skip": True}},
    )
    assert result["skipped"] is True


def test_handle_demultiplex_status_skipped(tmp_path: Path) -> None:
    from methyl_worker.handlers.sample_prep import _handle_demultiplex
    from methyl_worker.task_models.sample_prep_models import DemultiplexTaskInput

    sample_dir = tmp_path / "S3"
    sample_dir.mkdir()
    _write_fastq(sample_dir / "S3_R1.fastq.gz", [("r1", "ACGT", "IIII")])
    _write_fastq(sample_dir / "S3_R2.fastq.gz", [("r1", "TTTT", "IIII")])
    out = _handle_demultiplex(
        "sample.demultiplex",
        "sample.demultiplex",
        DemultiplexTaskInput(
            sampleId="S3",
            sampleDir=str(sample_dir),
            resolvedConfig={"skip": True},
        ),
    )
    assert out.status == "skipped"
    assert out.skipped is True


def test_handle_demultiplex_status_ok(tmp_path: Path) -> None:
    from methyl_worker.handlers.sample_prep import _handle_demultiplex
    from methyl_worker.task_models.sample_prep_models import DemultiplexTaskInput

    sample_dir = tmp_path / "S4"
    sample_dir.mkdir()
    _write_fastq(
        sample_dir / "S4_R1.fastq.gz",
        [("r1", "ACGTAAAA", "IIIIIIII")],
    )
    _write_fastq(
        sample_dir / "S4_R2.fastq.gz",
        [("r1", "GGGGGGGG", "IIIIIIII")],
    )
    barcodes = tmp_path / "barcodes.tsv"
    barcodes.write_text("sample_id\tbarcode\nS4\tACGT\n", encoding="utf-8")
    out = _handle_demultiplex(
        "sample.demultiplex",
        "sample.demultiplex",
        DemultiplexTaskInput(
            sampleId="S4",
            sampleDir=str(sample_dir),
            resolvedConfig={"barcode_tsv": str(barcodes)},
        ),
    )
    assert out.status == "ok"
    assert out.skipped is False
