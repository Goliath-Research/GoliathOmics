"""Offline tests for sync_cfg_profiles_and_action_catalog helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "sync_cfg_profiles_and_action_catalog",
    ROOT / "scripts" / "sync_cfg_profiles_and_action_catalog.py",
)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def test_cfg_status_published():
    assert sync._cfg_status({"lifecycle": "active", "visibility": "operator"}) == "published"


def test_cfg_status_retired():
    assert sync._cfg_status({"lifecycle": "deprecated", "visibility": "operator"}) == "retired"


def test_iter_analytes_from_repo():
    rows = sync._iter_analytes(ROOT)
    names = {r[0] for r in rows}
    assert "buffy_coat" in names
    assert "cfdna" in names


def test_iter_profiles_skips_site_examples():
    rows = sync._iter_profiles(ROOT)
    names = {r[0] for r in rows}
    assert not any(n.startswith("site_") for n in names)
    assert any("samd" in n for n in names)


def test_iter_procedures_has_analyte_expectation():
    rows = sync._iter_procedures(ROOT)
    assert rows
    buffy = [r for r in rows if r[0] == "buffy_wgbs_linear_gene_fc"]
    assert buffy
    assert buffy[0][4] == "buffy_coat"
