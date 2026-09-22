"""MHL formula and discovery smoke."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from methyl_mhl.core.mhl import mhl_for_block
from methyl_mhl.core.discovery import discover_mhbs
from methyl_mhl.core.runner import run_mhb_mhl_for_project
from methyl_mhl.config import MhbMhlStepConfig
from methyl_utils.core.mhap_io import MhapStore, write_mhap_store


def _store(chrom: str, starts, strands, positions, meth) -> MhapStore:
    n_cpg = [len(p) for p in positions]
    return MhapStore(
        chrom=chrom,
        context="CG",
        read_start=np.asarray(starts, dtype=np.int32),
        read_strand=np.asarray(strands, dtype=np.int8),
        n_cpg=np.asarray(n_cpg, dtype=np.int32),
        cpg_pos=np.asarray([p for row in positions for p in row], dtype=np.int32),
        meth=np.asarray([m for row in meth for m in row], dtype=np.uint8),
    )


def test_fully_methylated_block_has_mhl_one():
    store = _store(
        "21",
        [100, 100, 100],
        [1, 1, 1],
        [[10, 12, 14], [10, 12, 14], [10, 12, 14]],
        [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
    )
    assert mhl_for_block([store], 10, 16, max_len=3) == 1.0


def test_unmethylated_block_has_mhl_zero():
    store = _store(
        "21",
        [100],
        [1],
        [[10, 12, 14]],
        [[0, 0, 0]],
    )
    assert mhl_for_block([store], 10, 16, max_len=3) == 0.0


def test_discovery_joins_correlated_sites():
    # Three samples, three adjacent CpGs always co-methylated.
    stores = [
        _store("21", [0, 0], [1, 1], [[10, 12, 14], [10, 12, 14]], [[1, 1, 1], [0, 0, 0]]),
        _store("21", [0, 0], [1, 1], [[10, 12, 14], [10, 12, 14]], [[1, 1, 1], [0, 0, 0]]),
        _store("21", [0, 0], [1, 1], [[10, 12, 14], [10, 12, 14]], [[1, 1, 1], [0, 0, 0]]),
    ]
    blocks = discover_mhbs(
        stores,
        r2_min=0.3,
        p_max=0.05,
        core_window=3,
        min_cpgs=3,
        min_median_reads=2,
        chrom="21",
    )
    assert not blocks.empty
    assert int(blocks.iloc[0]["n_cpgs"]) >= 3


def test_runner_locked_bed(tmp_path: Path):
    sample = tmp_path / "s1"
    sample.mkdir()
    store = _store("21", [0], [1], [[10, 12, 14]], [[1, 1, 0]])
    write_mhap_store(sample / "21-CG.mhap.h5", store)
    bed = tmp_path / "locked.bed"
    bed.write_text("21\t10\t16\tblk1\n", encoding="utf-8")
    cfg = MhbMhlStepConfig(
        mode="locked",
        mhb_bed=str(bed),
        mhl_max_length=3,
        chromosomes=["21"],
    )
    out = tmp_path / "out"
    summary = run_mhb_mhl_for_project(
        [("s1", str(sample), "g")],
        out,
        cfg,
        project_chromosomes=["21"],
    )
    assert summary["status"] == "ok"
    assert summary["n_blocks"] == 1
    matrix = pd.read_csv(summary["mhl_matrix"])
    assert matrix.shape[0] == 1
    assert "blk1" in matrix.columns


def test_panel_restricted_mhl_then_cox_smoke(tmp_path: Path):
    """CI smoke: tiny panel mhap → MHL matrix shape → Cox artifact keys."""
    from methyl_validation.survival_backend import run_survival_model

    samples = []
    for i in range(6):
        sample = tmp_path / f"s{i}"
        sample.mkdir()
        bits = [1, 1, 1] if i < 3 else [0, 0, 0]
        write_mhap_store(
            sample / "21-CG.mhap.h5",
            _store("21", [0], [1], [[10, 12, 14]], [bits]),
        )
        samples.append((f"s{i}", str(sample), "g"))
    bed = tmp_path / "panel_mhb.bed"
    bed.write_text("21\t10\t16\tblk1\n", encoding="utf-8")
    cfg = MhbMhlStepConfig(
        mode="locked",
        mhb_bed=str(bed),
        mhl_max_length=3,
        chromosomes=["21"],
    )
    mhl_out = tmp_path / "mhb_mhl"
    summary = run_mhb_mhl_for_project(samples, mhl_out, cfg, project_chromosomes=["21"])
    assert summary["status"] == "ok"
    matrix = pd.read_csv(summary["mhl_matrix"])
    assert matrix.shape == (6, 3)  # sample_id, group, blk1
    assert "blk1" in matrix.columns

    surv = tmp_path / "survival.csv"
    pd.DataFrame(
        {
            "sample_id": [f"s{i}" for i in range(6)],
            "time": [4, 6, 8, 10, 12, 14],
            "event": [1, 1, 1, 0, 0, 0],
        }
    ).to_csv(surv, index=False)
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "mhl_smoke",
                "output_base": str(tmp_path),
                "group1": {
                    "label": "a",
                    "sample_paths": [str(tmp_path / f"s{i}") for i in range(3)],
                },
                "group2": {
                    "label": "b",
                    "sample_paths": [str(tmp_path / f"s{i}") for i in range(3, 6)],
                },
                "survival_path": str(surv),
                "chromosomes": ["21"],
            }
        ),
        encoding="utf-8",
    )
    from types import SimpleNamespace

    params = SimpleNamespace(
        mhl_matrix_path=summary["mhl_matrix"],
        clinical_columns=None,
        time_auc_horizons=[8],
        write_nomogram=True,
        nested_lrt=False,
    )
    cfg_val = SimpleNamespace(get_backend_params=lambda _name: params)
    metrics = run_survival_model(
        project_json=project,
        output_dir=tmp_path / "cox",
        config=cfg_val,
    )
    assert metrics["backend"] == "cox"
    assert "concordance" in metrics
    assert (tmp_path / "cox" / "survival_metrics.json").is_file()
    assert (tmp_path / "cox" / "nomogram.json").is_file()

