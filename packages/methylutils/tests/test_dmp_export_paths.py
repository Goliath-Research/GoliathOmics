"""Tests for dual-branch DMP CSV path resolution."""

from __future__ import annotations

from pathlib import Path

from methyl_utils.dmp_export_paths import (
    find_classifier_dmps_csvs,
    find_discovery_dmps_csvs,
    first_classifier_dmps_csv,
    glob_discovery_dmps_with_unified_fallback,
)


def test_classifier_prefers_suffix_over_legacy(tmp_path: Path) -> None:
    (tmp_path / "dmps-1-classifier.csv").write_text("a\n")
    (tmp_path / "dmps-1-discovery.csv").write_text("b\n")
    assert [p.name for p in find_classifier_dmps_csvs(tmp_path)] == ["dmps-1-classifier.csv"]
    assert first_classifier_dmps_csv(tmp_path).name == "dmps-1-classifier.csv"


def test_classifier_legacy_dmps_only(tmp_path: Path) -> None:
    (tmp_path / "dmps-1.csv").write_text("a\n")
    assert [p.name for p in find_classifier_dmps_csvs(tmp_path)] == ["dmps-1.csv"]


def test_classifier_excludes_discovery(tmp_path: Path) -> None:
    (tmp_path / "dmps-1-discovery.csv").write_text("b\n")
    (tmp_path / "dmps-2.csv").write_text("a\n")
    names = [p.name for p in find_classifier_dmps_csvs(tmp_path)]
    assert "dmps-1-discovery.csv" not in names
    assert "dmps-2.csv" in names


def test_discovery_glob(tmp_path: Path) -> None:
    (tmp_path / "dmps-1-discovery.csv").write_text("b\n")
    (tmp_path / "dmps-1-classifier.csv").write_text("a\n")
    assert [p.name for p in find_discovery_dmps_csvs(tmp_path)] == ["dmps-1-discovery.csv"]


def test_discovery_fallback_to_unified(tmp_path: Path) -> None:
    (tmp_path / "dmps-X.csv").write_text("a\n")
    assert [p.name for p in find_discovery_dmps_csvs(tmp_path)] == ["dmps-X.csv"]


def test_glob_discovery_unified_fallback_single_dir(tmp_path: Path) -> None:
    (tmp_path / "dmps-1.csv").write_text("a\n")
    assert glob_discovery_dmps_with_unified_fallback(tmp_path, "dmps-*-discovery.csv") == [
        tmp_path / "dmps-1.csv"
    ]


def test_glob_discovery_unified_fallback_nested_comparisons(tmp_path: Path) -> None:
    comp = tmp_path / "all" / "pca_pca1"
    comp.mkdir(parents=True)
    (comp / "dmps-1.csv").write_text("a\n")
    found = glob_discovery_dmps_with_unified_fallback(tmp_path, "*/*/dmps-*-discovery.csv")
    assert [p.name for p in found] == ["dmps-1.csv"]
    assert found[0].parent == comp


def test_glob_discovery_unified_fallback_not_discovery_pattern(tmp_path: Path) -> None:
    (tmp_path / "dmps-1.csv").write_text("a\n")
    assert glob_discovery_dmps_with_unified_fallback(tmp_path, "dmps-*.csv") == []
