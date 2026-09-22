"""Tests for shared MC centroid seed baseline copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from methyl_validation.project_gen import copy_centroid_seed_baseline


def test_copy_centroid_seed_baseline_copies_tree(tmp_path: Path) -> None:
    seed = tmp_path / "seed" / "controls" / "healthy" / "healthy"
    seed.mkdir(parents=True)
    (seed / "21-CG.h5").write_bytes(b"seed")
    out = tmp_path / "run_0001" / "centroids" / "controls" / "healthy" / "healthy"
    assert copy_centroid_seed_baseline(seed, out)
    assert (out / "21-CG.h5").is_file()


def test_copy_centroid_seed_baseline_missing_seed_returns_false(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert not copy_centroid_seed_baseline(tmp_path / "missing", out)


def test_copy_centroid_seed_baseline_empty_seed_raises(tmp_path: Path) -> None:
    """A present-but-empty seed dir must fail loudly, not copy an empty tree."""
    seed = tmp_path / "seed" / "controls" / "healthy" / "all"
    seed.mkdir(parents=True)
    # Only manifests, no *.h5 (the exact MC failure signature).
    (seed / ".action_results").mkdir()
    (seed / ".action_results" / "pipeline_centroid.1_CG_all.json").write_text("{}")
    out = tmp_path / "run_0001" / "centroids" / "controls" / "healthy" / "all"
    with pytest.raises(FileNotFoundError, match="no \\*.h5 centroids"):
        copy_centroid_seed_baseline(seed, out)
    assert not out.exists()
