"""Tests for fastp trim FASTQ discovery."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from methyl_worker.fastq_trim_runner import _staging_fastq_path, run_fastp_trim


def test_staging_fastq_path_keeps_gzip_suffix() -> None:
    final = Path("/work/samples/S1/S1_1.trimmed.fastq.gz")
    staging = _staging_fastq_path(final)
    assert staging.name == "S1_1.trimmed.tmp.fastq.gz"
    assert staging.name.endswith(".gz")
    assert staging.parent == final.parent
    assert staging != final


def test_trim_discovers_noncanonical_paired_fastqs(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S_lanes"
    sample_dir.mkdir()
    r1 = sample_dir / "S_lanes_L001_R1_001.fastq.gz"
    r2 = sample_dir / "S_lanes_L001_R2_001.fastq.gz"
    r1.write_bytes(b"r1")
    r2.write_bytes(b"r2")

    def fake_run(cmd, **kwargs):
        out_r1 = Path(cmd[cmd.index("-o") + 1])
        out_r2 = Path(cmd[cmd.index("-O") + 1])
        out_r1.write_bytes(b"t1")
        out_r2.write_bytes(b"t2")
        class _P:
            returncode = 0
            stdout = ""
            stderr = ""
        return _P()

    with patch("methyl_worker.fastq_trim_runner.shutil.which", return_value="fastp"):
        with patch("methyl_worker.fastq_trim_runner.subprocess.run", side_effect=fake_run):
            out = run_fastp_trim(
                sample_id="S_lanes",
                sample_dir=sample_dir,
                input_json={"trimFront2": 5},
            )
    assert Path(out["trimmedR1"]).name == "S_lanes_1.trimmed.fastq.gz"
    assert Path(out["trimmedR2"]).is_file()


def test_trim_retry_does_not_feed_trimmed_outputs_into_fastp(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S_retry"
    sample_dir.mkdir()
    r1 = sample_dir / "S_retry_1.fastq.gz"
    r2 = sample_dir / "S_retry_2.fastq.gz"
    r1.write_bytes(b"original-r1")
    r2.write_bytes(b"original-r2")
    existing_r1 = sample_dir / "S_retry_1.trimmed.fastq.gz"
    existing_r2 = sample_dir / "S_retry_2.trimmed.fastq.gz"
    existing_r1.write_bytes(b"already-trimmed-r1")
    existing_r2.write_bytes(b"already-trimmed-r2")

    seen: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        r1_in = Path(cmd[cmd.index("-i") + 1])
        r2_in = Path(cmd[cmd.index("-I") + 1])
        out_r1 = Path(cmd[cmd.index("-o") + 1])
        out_r2 = Path(cmd[cmd.index("-O") + 1])
        seen["r1_in"] = r1_in.name
        seen["r2_in"] = r2_in.name
        seen["r1_out"] = out_r1.name
        seen["r2_out"] = out_r2.name
        assert r1_in.resolve() != out_r1.resolve()
        assert r2_in.resolve() != out_r2.resolve()
        assert r1_in.read_bytes() == b"original-r1"
        out_r1.write_bytes(b"fresh-trim-r1")
        out_r2.write_bytes(b"fresh-trim-r2")

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()

    with patch("methyl_worker.fastq_trim_runner.shutil.which", return_value="fastp"):
        with patch("methyl_worker.fastq_trim_runner.subprocess.run", side_effect=fake_run):
            out = run_fastp_trim(
                sample_id="S_retry",
                sample_dir=sample_dir,
                input_json={"trimFront2": 5},
            )

    assert seen["r1_in"] == "S_retry_1.fastq.gz"
    assert seen["r2_in"] == "S_retry_2.fastq.gz"
    assert seen["r1_out"] == "S_retry_1.trimmed.tmp.fastq.gz"
    assert seen["r2_out"].endswith(".gz")
    assert Path(out["trimmedR1"]).read_bytes() == b"fresh-trim-r1"
    assert Path(out["trimmedR2"]).read_bytes() == b"fresh-trim-r2"
    assert not (sample_dir / "S_retry_1.trimmed.tmp.fastq.gz").exists()
    assert not (sample_dir / "S_retry_1.trimmed.fastq.gz.tmp").exists()


def test_trim_does_not_use_trimmed_outputs_as_only_inputs(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S_only_trim"
    sample_dir.mkdir()
    (sample_dir / "S_only_trim_1.trimmed.fastq.gz").write_bytes(b"t1")
    (sample_dir / "S_only_trim_2.trimmed.fastq.gz").write_bytes(b"t2")
    with pytest.raises(RuntimeError, match="paired FASTQ"):
        run_fastp_trim(
            sample_id="S_only_trim",
            sample_dir=sample_dir,
            input_json={"trimFront2": 5},
        )


def test_trim_requires_paired_fastqs(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S_empty"
    sample_dir.mkdir()
    with pytest.raises(RuntimeError, match="paired FASTQ"):
        run_fastp_trim(
            sample_id="S_empty",
            sample_dir=sample_dir,
            input_json={"trimFront2": 5},
        )


def test_clara_trim_does_not_import_mojo_or_parabricks_runner() -> None:
    """fastp trim must not pull Clara/Mojo aligner modules at import time."""
    import methyl_worker.fastq_trim_runner as trim

    source = Path(trim.__file__).read_text(encoding="utf-8")
    assert "fastq_pairs" in source
    assert "parabricks_runner" not in source
    assert "mojo_align" not in source
    import methyl_worker.fastq_pairs as pairs

    pair_src = Path(pairs.__file__).read_text(encoding="utf-8")
    assert "mojo_align" not in pair_src
    assert "parabricks_runner" not in pair_src
