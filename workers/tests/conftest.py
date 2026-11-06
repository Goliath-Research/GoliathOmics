"""Keep worker-local scratch (BAMs, MethylExtractor output) inside each test's tmp_path."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# CI installs editable goliath-gateway from GoliathApp; pytest's pythonpath still
# prefers this repo's workflow_engine fixture tree, which shadows ``import domain``.
# Prepend the gateway tree so domain.program resolves to the platform IR.
_gateway_root = os.environ.get("GOLIATH_GATEWAY_WORKFLOW_ENGINE")
if _gateway_root:
    _gw = Path(_gateway_root).resolve()
    if _gw.is_dir():
        _s = str(_gw)
        if _s in sys.path:
            sys.path.remove(_s)
        sys.path.insert(0, _s)


@pytest.fixture(autouse=True)
def _isolated_local_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METHYL_LOCAL_TMP", str(tmp_path / "_local_scratch"))
