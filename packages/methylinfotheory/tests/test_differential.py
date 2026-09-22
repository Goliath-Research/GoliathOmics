"""Tests for cohort differential Ising records."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from methyl_infotheory.config import InfoTheoryStepConfig
from methyl_infotheory.core.differential import compute_differential_records
from methyl_utils.core.read_level_io import ReadLevelPatterns, write_read_level_patterns


def _write_sample(sample_dir: Path, hist: dict):
    sample_dir.mkdir(parents=True, exist_ok=True)
    tile_ids = []
    pattern_ids = []
    counts = []
    for pid, count in hist.items():
        tile_ids.append(0)
        pattern_ids.append(int(pid))
        counts.append(int(count))
    data = ReadLevelPatterns(
        context="CG",
        tile_size=2,
        tile_start_pos=np.array([1000], dtype=np.uint32),
        tile_cpg_positions=np.array([[1000, 1001]], dtype=np.uint32),
        tile_n_reads=np.array([sum(hist.values())], dtype=np.uint32),
        pattern_tile_id=np.array(tile_ids, dtype=np.uint32),
        pattern_id=np.array(pattern_ids, dtype=np.uint16),
        pattern_count=np.array(counts, dtype=np.uint32),
    )
    write_read_level_patterns(sample_dir / "1-CG.patterns.h5", data)


def test_differential_near_zero_for_identical_groups(tmp_path):
    g1 = tmp_path / "s1"
    g2 = tmp_path / "s2"
    _write_sample(g1, {0: 50, 3: 50})
    _write_sample(g2, {0: 40, 3: 40})
    cfg = InfoTheoryStepConfig(
        ising_enabled=True,
        jsd_min_cohort_reads=10,
        jsd_top_windows=10,
        ising_max_iter=50,
        ising_l2=1e-3,
    )
    records = compute_differential_records(
        [str(g1)], [str(g2)], chromosomes=["1"], contexts=["CG"], cfg=cfg
    )
    assert len(records) == 1
    assert abs(records[0].dmml) < 0.1
    assert abs(records[0].dnme) < 0.1
    assert records[0].model_jsd == pytest.approx(0.0, abs=0.05)


def test_differential_large_jsd_for_disjoint_patterns(tmp_path):
    g1 = tmp_path / "s1"
    g2 = tmp_path / "s2"
    _write_sample(g1, {0: 90, 3: 10})
    _write_sample(g2, {0: 10, 3: 90})
    cfg = InfoTheoryStepConfig(
        ising_enabled=True,
        jsd_min_cohort_reads=10,
        jsd_top_windows=10,
        ising_max_iter=50,
        ising_l2=1e-3,
    )
    records = compute_differential_records(
        [str(g1)], [str(g2)], chromosomes=["1"], contexts=["CG"], cfg=cfg
    )
    assert len(records) == 1
    assert records[0].model_jsd > 0.1
