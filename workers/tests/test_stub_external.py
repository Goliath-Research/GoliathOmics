"""Tests for WORKER_STUB_EXTERNAL routing in execute_task."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from methyl_worker.handlers import execute_task


@pytest.fixture(autouse=True)
def _clear_stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_STUB_EXTERNAL", raising=False)


def test_stub_methyl_extract_writes_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_STUB_EXTERNAL", "1")
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()

    result = execute_task(
        "methyl-extract",
        "sample.methyl_extract",
        {"sampleId": "S1", "sampleDir": str(sample_dir), "projectPath": "/tmp/project.json"},
    )
    out = result.output.model_dump()
    assert out["h5Files"] == ["21-CG.h5"]
    assert (sample_dir / "21-CG.h5").is_file()
    manifest = json.loads((sample_dir / "S1.extraction_manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["cpg_weighted_mean_coverage"] == 20.0


def test_archive_sample_runs_real_handler_with_file_destination(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    archive_root = tmp_path / "archive"
    (sample_dir / "21-CG.h5").write_bytes(b"h5")
    (sample_dir / "S1.extraction_qc.json").write_text("{}", encoding="utf-8")

    result = execute_task(
        "sample.archive-sample",
        "sample.archive_sample",
        {
            "sampleId": "S1",
            "sampleDir": str(sample_dir),
            "mode": "full",
            "sampleDestination": {
                "type": "file",
                "basePath": str(archive_root),
                "prefix": "S1/",
            },
        },
    )
    assert result.output.model_dump()["archiveMode"] == "full"
    assert (archive_root / "S1" / "h5" / "21-CG.h5").read_bytes() == b"h5"
