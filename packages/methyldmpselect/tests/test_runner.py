"""Tests for methyl-dmp-select runner."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from methyl_dmp_select.core.runner import run_dmp_selection
from methyl_dmp_select.models.config import DmpSelectionConfig


class _ReadOnlyChromosomeDetector:
    """MethylDetector stand-in: chromosome is read-only (no setter)."""

    def __init__(self, config) -> None:
        self.config = config

    @property
    def chromosome(self) -> str:
        chrom = self.config.chromosome
        return chrom[0] if isinstance(chrom, list) else str(chrom)

    def run_dmp_panel_selection_from_discovery(self, _df, *, export_classifier_pickle=False):
        return {"n_classifier": 1, "n_extended": 2}


def test_run_dmp_selection_uses_config_chromosome(tmp_path) -> None:
    c1 = tmp_path / "c1"
    c2 = tmp_path / "c2"
    c1.mkdir()
    c2.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    discovery = out / "dmps-21-discovery.csv"
    discovery.write_text(
        "chromosome,position,context,effect_size\n21,100,CG,0.5\n",
        encoding="utf-8",
    )
    config = DmpSelectionConfig(
        chromosome="21",
        centroid1_dir=str(c1),
        centroid2_dir=str(c2),
        output_dir=str(out),
        discovery_csv=str(discovery),
    )

    with patch(
        "methyl_detector.core.methyldetector.MethylDetector",
        _ReadOnlyChromosomeDetector,
    ):
        result = run_dmp_selection(config, skip_if_exists=False)

    assert result["status"] == "ok"
    assert (out / "dmp_selection-21.json").is_file()
