"""Tests for sample_prep_log JSONL append helper."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_worker.sample_prep_log import append_sample_prep_log
from methyl_worker.work_share import share_work_path


def test_append_sample_prep_log_preserves_prior_lines(tmp_path: Path):
    sample_dir = tmp_path / "sampleX"
    sample_dir.mkdir()
    append_sample_prep_log(
        sample_dir,
        sample_id="sampleX",
        action="sample.trim_fastq",
        capability="sample.trim-fastq",
        reason="trim test",
        inputs={"trimFront2": 5},
        outputs={"trimmedR2": "x"},
    )
    append_sample_prep_log(
        sample_dir,
        sample_id="sampleX",
        action="sample.methyl_qc",
        capability="methyl-qc",
        attempt=2,
        reason="retry after trim",
    )
    log_path = sample_dir / "sampleX.sample_prep_log.jsonl"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["action"] == "sample.trim_fastq"
    assert second["attempt"] == 2


def test_append_sample_prep_log_retries_after_eacces(tmp_path: Path, monkeypatch) -> None:
    sample_dir = tmp_path / "locked"
    sample_dir.mkdir()
    original = Path.open
    state = {"n": 0}

    def flaky(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if self.name.endswith(".sample_prep_log.jsonl") and "a" in str(mode):
            state["n"] += 1
            if state["n"] == 1:
                raise PermissionError("EACCES")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky)
    append_sample_prep_log(
        sample_dir,
        sample_id="locked",
        action="sample.methyl_qc",
        capability="methyl-qc",
    )
    assert state["n"] == 2
    log_path = sample_dir / "locked.sample_prep_log.jsonl"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["action"] == "sample.methyl_qc"


def test_share_work_path_opens_mode_600(tmp_path: Path) -> None:
    f = tmp_path / "out.bin"
    f.write_bytes(b"x")
    f.chmod(0o600)
    share_work_path(f)
    assert f.stat().st_mode & 0o006 == 0o006
