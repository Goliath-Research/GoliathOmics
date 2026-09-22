"""Unit tests for the test-data registry model, loader precedence, and resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_utils.test_data_registry import (
    SITE_CONFIG_ENV,
    TEST_DATA_CONFIG_ENV,
    TestDataRegistry,
    load_test_data_registry,
    resolve_sample_dir,
    sample_has_h5,
)


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_registry_parses_samples_and_groups() -> None:
    reg = TestDataRegistry.model_validate(
        {
            "samples": {
                "cfdna": {"sample_id": "R1", "sample_dir": "/work/samples/R1", "analyte": "cfdna"},
            },
            "groups": {
                "healthy": {"label": "healthy", "sample_dirs": ["/work/samples/H1"]},
            },
        }
    )
    assert reg.samples["cfdna"].sample_id == "R1"
    assert reg.groups["healthy"].sample_dirs == ["/work/samples/H1"]


def test_registry_rejects_unknown_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TestDataRegistry.model_validate({"samples": {"cfdna": {"bogus_field": 1}}})


def test_explicit_env_path_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _write(tmp_path / "reg.json", {"samples": {"cfdna": {"sample_id": "FROM_ENV"}}})
    monkeypatch.setenv(TEST_DATA_CONFIG_ENV, str(cfg))
    monkeypatch.delenv(SITE_CONFIG_ENV, raising=False)

    reg = load_test_data_registry()
    assert reg.samples["cfdna"].sample_id == "FROM_ENV"


def test_site_manifest_testing_block_used_when_no_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _write(
        tmp_path / "methyl_site.json",
        {"testing": {"samples": {"buffy_coat": {"sample_id": "FROM_SITE"}}}},
    )
    monkeypatch.delenv(TEST_DATA_CONFIG_ENV, raising=False)
    monkeypatch.setenv(SITE_CONFIG_ENV, str(site))

    reg = load_test_data_registry()
    assert reg.samples["buffy_coat"].sample_id == "FROM_SITE"


def test_resolve_sample_dir_absolute_and_relative() -> None:
    assert resolve_sample_dir("/work/samples/X") == Path("/work/samples/X")
    rel = resolve_sample_dir("tests/real_data/fixtures")
    assert rel is not None and rel.is_absolute()
    assert resolve_sample_dir(None) is None


def test_sample_has_h5_detects_files(tmp_path: Path) -> None:
    assert sample_has_h5(tmp_path) is False
    (tmp_path / "21-CG.h5").write_bytes(b"\x00")
    assert sample_has_h5(tmp_path) is True
    assert sample_has_h5(tmp_path / "missing") is False
