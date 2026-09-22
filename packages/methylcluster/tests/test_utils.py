"""Unit tests for MethylCluster utility helpers (path validation, stats, summary)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from methyl_cluster.utils import (
    compute_distance_statistics,
    create_cluster_summary,
    validate_sample_paths,
)


def test_validate_sample_paths_returns_dirs_when_h5_present(tmp_path: Path) -> None:
    dirs = []
    for name in ("s1", "s2"):
        d = tmp_path / name
        d.mkdir()
        (d / "1-CG.h5").write_bytes(b"\x00")
        dirs.append(str(d))

    validated = validate_sample_paths(dirs, chrom="1", ctx="CG")
    assert [p.name for p in validated] == ["s1", "s2"]


def test_validate_sample_paths_raises_on_missing_h5(tmp_path: Path) -> None:
    d = tmp_path / "s1"
    d.mkdir()  # directory exists but no H5 file
    with pytest.raises(FileNotFoundError, match="HDF5 file not found"):
        validate_sample_paths([str(d)], chrom="1", ctx="CG")


def test_validate_sample_paths_raises_on_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Directory not found"):
        validate_sample_paths([str(tmp_path / "nope")], chrom="1", ctx="CG")


def test_compute_distance_statistics_uses_upper_triangle() -> None:
    matrix = np.array(
        [
            [0.0, 1.0, 3.0],
            [1.0, 0.0, 5.0],
            [3.0, 5.0, 0.0],
        ]
    )
    stats = compute_distance_statistics(matrix)
    # Upper-triangle (excl. diagonal) distances are {1, 3, 5}.
    assert stats["min"] == 1.0
    assert stats["max"] == 5.0
    assert stats["mean"] == pytest.approx(3.0)
    assert stats["median"] == pytest.approx(3.0)


def test_create_cluster_summary_counts_clusters_and_noise() -> None:
    labels = np.array([0, 0, 1, -1])
    sample_paths = [Path(f"/data/s{i}") for i in range(4)]

    summary = create_cluster_summary(labels, sample_paths)
    assert summary["n_clusters"] == 2  # noise label (-1) excluded
    assert summary["n_noise"] == 1
    assert summary["n_samples"] == 4
    assert summary["clusters"]["cluster_0"]["size"] == 2
    assert summary["clusters"]["noise"]["samples"] == ["/data/s3"]
