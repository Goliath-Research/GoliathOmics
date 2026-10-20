"""Keep worker-local scratch (BAMs, MethylExtractor output) inside each test's tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_local_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METHYL_LOCAL_TMP", str(tmp_path / "_local_scratch"))


def local_bam(sample_dir: Path, sample_id: str) -> Path:
    """Worker-local BAM path for ``sample_id`` (parent created)."""
    from methyl_utils.local_align import local_bam_path

    bam = local_bam_path(sample_id, sample_dir)
    bam.parent.mkdir(parents=True, exist_ok=True)
    return bam
