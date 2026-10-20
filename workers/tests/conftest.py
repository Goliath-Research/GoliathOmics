"""Keep worker-local scratch (BAMs, MethylExtractor output) inside each test's tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_local_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METHYL_LOCAL_TMP", str(tmp_path / "_local_scratch"))
