"""Tests for deployed profile path resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from methyl_utils.profile_paths import profile_search_dirs, resolve_profile_path


def test_resolve_profile_path_by_name_from_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "demo.profile.json").write_text(
        json.dumps({"pipelineProfile": "demo", "actionConfig": {}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_PROFILE_DIR", str(profiles))
    resolved = resolve_profile_path("demo")
    assert resolved == (profiles / "demo.profile.json").resolve()


def test_resolve_profile_path_alias_buffy_mc_gene_fc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "mc_dmp_gene_fc.profile.json").write_text(
        json.dumps({"pipelineProfile": "mc_dmp_gene_fc", "actionConfig": {}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_PROFILE_DIR", str(profiles))
    resolved = resolve_profile_path("buffy_mc_gene_fc")
    assert resolved.name == "mc_dmp_gene_fc.profile.json"


def test_resolve_profile_path_alias_mc_dmp_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "mc_dmp.profile.json").write_text(
        json.dumps({"pipelineProfile": "mc_dmp", "actionConfig": {}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_PROFILE_DIR", str(profiles))
    resolved = resolve_profile_path("mc_dmp_discovery")
    assert resolved.name == "mc_dmp.profile.json"


def test_resolve_profile_path_explicit_file(tmp_path: Path) -> None:
    profile = tmp_path / "custom.profile.json"
    profile.write_text("{}", encoding="utf-8")
    assert resolve_profile_path(profile) == profile.resolve()


def test_profile_search_dirs_includes_goliath_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METHYL_PROFILE_DIR", raising=False)
    monkeypatch.setenv("GOLIATH_ROOT", "/work/goliath")
    dirs = profile_search_dirs()
    assert any(str(d).endswith("runtime-bundle/domain/profiles") for d in dirs)
