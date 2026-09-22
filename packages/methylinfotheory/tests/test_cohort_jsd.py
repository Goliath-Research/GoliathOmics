"""Tests for cohort Jensen-Shannon distance."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from methyl_infotheory.config import InfoTheoryStepConfig
from methyl_infotheory.core.cohort_jsd import compute_cohort_jsd_records
from methyl_utils.core.read_level_io import ReadLevelPatterns, write_read_level_patterns


def _write_sample_patterns(sample_dir: Path, *, chrom: str, hist_g1: dict, hist_g2: dict | None = None):
    sample_dir.mkdir(parents=True, exist_ok=True)
    if hist_g2 is None:
        hist = hist_g1
    else:
        hist = hist_g1
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
    write_read_level_patterns(sample_dir / f"{chrom}-CG.patterns.h5", data)


def test_cohort_jsd_zero_for_identical_distributions(tmp_path):
    g1 = tmp_path / "s1"
    g2 = tmp_path / "s2"
    _write_sample_patterns(g1, chrom="1", hist_g1={0: 50, 3: 50})
    _write_sample_patterns(g2, chrom="1", hist_g1={0: 40, 3: 40})
    cfg = InfoTheoryStepConfig(jsd_min_cohort_reads=10, jsd_top_windows=10)
    records = compute_cohort_jsd_records(
        [str(g1)],
        [str(g2)],
        chromosomes=["1"],
        contexts=["CG"],
        cfg=cfg,
    )
    assert len(records) == 1
    assert records[0].jsd == pytest.approx(0.0, abs=1e-6)


def test_cohort_jsd_positive_for_different_distributions(tmp_path):
    g1 = tmp_path / "s1"
    g2 = tmp_path / "s2"
    _write_sample_patterns(g1, chrom="1", hist_g1={0: 90, 3: 10})
    _write_sample_patterns(g2, chrom="1", hist_g1={0: 10, 3: 90})
    cfg = InfoTheoryStepConfig(jsd_min_cohort_reads=10, jsd_top_windows=10)
    records = compute_cohort_jsd_records(
        [str(g1)],
        [str(g2)],
        chromosomes=["1"],
        contexts=["CG"],
        cfg=cfg,
    )
    assert len(records) == 1
    assert records[0].jsd > 0.1
