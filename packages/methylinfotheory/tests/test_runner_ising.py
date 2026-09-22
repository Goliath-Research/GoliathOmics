"""Runner integration tests for Ising v2 on/off."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_infotheory.config import InfoTheoryStepConfig
from methyl_infotheory.core.runner import run_info_measures_for_project
from methyl_utils.core.read_level_io import ReadLevelPatterns, write_read_level_patterns


def _write_sidecar(sample_dir: Path):
    sample_dir.mkdir(parents=True, exist_ok=True)
    data = ReadLevelPatterns(
        context="CG",
        tile_size=2,
        tile_start_pos=np.array([1000], dtype=np.uint32),
        tile_cpg_positions=np.array([[1000, 1001]], dtype=np.uint32),
        tile_n_reads=np.array([100], dtype=np.uint32),
        pattern_tile_id=np.array([0, 0], dtype=np.uint32),
        pattern_id=np.array([0, 3], dtype=np.uint16),
        pattern_count=np.array([50, 50], dtype=np.uint32),
    )
    write_read_level_patterns(sample_dir / "1-CG.patterns.h5", data)


def test_runner_skip_without_sidecars(tmp_path):
    out = tmp_path / "out"
    cfg = InfoTheoryStepConfig(ising_enabled=True)
    manifest = run_info_measures_for_project(
        [("s1", str(tmp_path / "missing"), "A")],
        out,
        cfg,
        project_chromosomes=["1"],
    )
    assert manifest["status"] == "skipped"


def test_runner_v1_columns_without_ising(tmp_path):
    s1 = tmp_path / "s1"
    s2 = tmp_path / "s2"
    _write_sidecar(s1)
    _write_sidecar(s2)
    out = tmp_path / "out_v1"
    cfg = InfoTheoryStepConfig(ising_enabled=False, min_tile_reads=1, jsd_min_cohort_reads=10)
    manifest = run_info_measures_for_project(
        [("s1", str(s1), "A"), ("s2", str(s2), "B")],
        out,
        cfg,
        project_chromosomes=["1"],
    )
    assert manifest["status"] == "ok"
    df = pd.read_csv(out / "readlevel_measures.csv")
    assert "readlevel::global_nme" not in df.columns
    assert not (out / "ising_regions.csv").exists()


def test_runner_ising_columns_and_regions(tmp_path):
    s1 = tmp_path / "s1"
    s2 = tmp_path / "s2"
    _write_sidecar(s1)
    _write_sidecar(s2)
    out = tmp_path / "out_v2"
    cfg = InfoTheoryStepConfig(
        ising_enabled=True,
        min_tile_reads=1,
        ising_min_tile_reads=1,
        jsd_min_cohort_reads=10,
        ising_max_iter=50,
        ising_l2=1e-3,
    )
    manifest = run_info_measures_for_project(
        [("s1", str(s1), "A"), ("s2", str(s2), "B")],
        out,
        cfg,
        project_chromosomes=["1"],
    )
    assert manifest["status"] == "ok"
    assert manifest["ising_enabled"] is True
    df = pd.read_csv(out / "readlevel_measures.csv")
    assert "readlevel::global_nme" in df.columns
    assert "readlevel::global_mml" in df.columns
    assert (out / "ising_regions.csv").exists()
