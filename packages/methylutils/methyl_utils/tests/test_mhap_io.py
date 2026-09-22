"""Haplotype store I/O and XM vs sequence+XG call parity."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from methyl_utils.core.meth_calls import meth_call_from_seq_xg, meth_call_prefer_xm
from methyl_utils.core.mhap_io import MhapStore, load_mhap_store, write_mhap_store


def test_mhap_roundtrip(tmp_path: Path):
    data = MhapStore(
        chrom="21",
        context="CG",
        read_start=np.array([10, 20], dtype=np.int32),
        read_strand=np.array([1, 2], dtype=np.int8),
        n_cpg=np.array([2, 1], dtype=np.int32),
        cpg_pos=np.array([100, 102, 200], dtype=np.int32),
        meth=np.array([1, 0, 1], dtype=np.uint8),
    )
    path = write_mhap_store(tmp_path / "21-CG.mhap.h5", data)
    loaded = load_mhap_store(path)
    assert loaded.n_reads == 2
    reads = list(loaded.iter_reads())
    assert reads[0][2].tolist() == [100, 102]
    assert reads[0][3].tolist() == [1, 0]


def test_prefer_xm_agrees_with_sequence_xg():
    assert meth_call_prefer_xm("Z", "C", "C", "CT") == 1
    assert meth_call_prefer_xm("z", "C", "T", "CT") == 0
    assert meth_call_prefer_xm(None, "C", "C", "CT") == 1
    assert meth_call_prefer_xm(".", "C", "T", "CT") == meth_call_from_seq_xg("C", "T", "CT")
    assert meth_call_prefer_xm("Z", "G", "G", "GA") == 1
