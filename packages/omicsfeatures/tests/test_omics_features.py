"""Unit tests for the shared samples-x-features seam."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from omics_features.de_select import DeSelectConfig, select_de_features
from omics_features.feature_store import read_sample_features, write_sample_features
from omics_features.matrix import load_feature_matrix


def _write(tmp: Path, sid: str, values: dict) -> str:
    d = tmp / sid
    d.mkdir(parents=True, exist_ok=True)
    write_sample_features(sample_dir=d, sample_id=sid, kind="abundance", values=values, source="test")
    return str(d)


def test_write_read_roundtrip(tmp_path: Path) -> None:
    _write(tmp_path, "s1", {"P1": 10.0, "P2": 0.0, "P3": 5.0})
    feats, vals, source = read_sample_features(tmp_path / "s1", "s1", kind="abundance")
    assert set(feats) == {"P1", "P2", "P3"}
    assert source == "test"
    assert vals.sum() == 15.0


def test_legacy_gene_id_count_h5_readable(tmp_path: Path) -> None:
    """Pre-omics_features RNA expression.h5 used gene_id/count datasets."""
    import h5py

    d = tmp_path / "s_legacy"
    d.mkdir()
    path = d / "s_legacy.expression.h5"
    with h5py.File(path, "w") as h5:
        dt = h5py.string_dtype(encoding="utf-8")
        h5.create_dataset("gene_id", data=np.asarray(["G1", "G2"], dtype=object), dtype=dt)
        h5.create_dataset("count", data=np.asarray([10.0, 20.0], dtype=np.float64))
        h5.attrs["quant_mode"] = "star_counts"
    feats, vals, source = read_sample_features(d, "s_legacy", kind="expression")
    assert list(feats) == ["G1", "G2"]
    assert list(vals) == [10.0, 20.0]
    assert source == "star_counts"
    X, gene_ids, sids = load_feature_matrix([str(d)], kind="expression", transform="none")
    assert X.shape == (1, 2)
    assert gene_ids == ["G1", "G2"]
    assert sids == ["s_legacy"]


def test_matrix_transforms_and_impute(tmp_path: Path) -> None:
    _write(tmp_path, "a", {"P1": 100.0, "P2": 50.0})
    _write(tmp_path, "b", {"P1": 100.0})  # P2 missing -> NaN then imputed
    dirs = [str(tmp_path / "a"), str(tmp_path / "b")]
    X, feats, sids = load_feature_matrix(
        dirs, kind="abundance", transform="log2", normalize="median", impute="min", missing_fill=float("nan")
    )
    assert X.shape == (2, 2)
    assert not np.any(np.isnan(X))  # imputed


def test_select_de_recovers_signal(tmp_path: Path) -> None:
    proteins = [f"P{i}" for i in range(30)]
    rng = np.random.default_rng(0)
    dirs = []
    labels = []
    for grp in (0, 1):
        for k in range(6):
            sid = f"g{grp}_{k}"
            vals = {p: float(rng.normal(5, 1)) for p in proteins}
            if grp == 1:
                for i in range(5):
                    vals[f"P{i}"] += 5
            _write(tmp_path, sid, vals)
            dirs.append(str(tmp_path / sid))
            labels.append(grp)
    X, feats, sids = load_feature_matrix(dirs, kind="abundance", transform="none", normalize="none", impute="none")
    panel = select_de_features(
        X, np.array(labels), feats, config=DeSelectConfig(kind="abundance", max_features=5)
    )
    top = set(panel["feature_id"])
    assert len({f"P{i}" for i in range(5)} & top) >= 4
