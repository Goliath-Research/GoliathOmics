"""On-target / control-region panel QC from extract H5."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from methyl_extraction_qc.core.panel_qc import compute_panel_qc


def _write_cg_h5(path: Path, pos: list[int], mc: list[float], uc: list[float]) -> None:
    import h5py

    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        grp = f.create_group("methylation_data")
        grp.create_dataset("pos", data=np.asarray(pos, dtype=np.int64))
        grp.create_dataset("mC", data=np.asarray(mc, dtype=np.float64))
        grp.create_dataset("uC", data=np.asarray(uc, dtype=np.float64))


def test_compute_panel_qc_on_target_and_controls(tmp_path: Path) -> None:
    sample = tmp_path / "s1"
    _write_cg_h5(sample / "21-CG.h5", [100, 200, 300], [9, 1, 0], [1, 9, 10])
    panel = tmp_path / "panel.bed"
    panel.write_text("21\t90\t210\n", encoding="utf-8")
    pos_ctl = tmp_path / "pos.bed"
    pos_ctl.write_text("21\t90\t110\n", encoding="utf-8")
    neg_ctl = tmp_path / "neg.bed"
    neg_ctl.write_text("21\t290\t310\n", encoding="utf-8")

    metrics = compute_panel_qc(
        sample,
        target_panel_bed=str(panel),
        pos_control_bed=str(pos_ctl),
        neg_control_bed=str(neg_ctl),
    )
    assert metrics["n_sites"] == 3
    assert metrics["n_on_target"] == 2
    assert metrics["on_target_fraction"] == pytest.approx(2 / 3)
    assert metrics["pos_control_mean_methylation"] == pytest.approx(0.9)
    assert metrics["neg_control_mean_methylation"] == pytest.approx(0.0)
