"""Integration: marker extract from miniature H5 + runner CSV."""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pytest

from methyl_deconv.config import CellDeconvRuntimeParams, CellDeconvStepConfig
from methyl_deconv.core.houseman import SeedBasis, deconvolve_sample, houseman_qp
from methyl_deconv.core.runner import run_cell_deconv_for_samples


def _write_sample_h5(path: Path, positions: np.ndarray, betas: np.ndarray, cov: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mC = np.round(betas * cov).astype(np.uint32)
    uC = (cov - mC).astype(np.uint32)
    tnc = np.zeros(len(positions), dtype=np.uint8)
    with h5py.File(path, "w") as f:
        g = f.create_group("methylation_data")
        g.create_dataset("pos", data=positions.astype(np.uint32))
        g.create_dataset("mC", data=mC)
        g.create_dataset("uC", data=uC)
        g.create_dataset("tnc", data=tnc)


def _tiny_basis(tmp_path: Path) -> Path:
    cell_types = ["CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"]
    # Identity-like extremes so QP recovers cleanly
    markers = []
    for i, ct in enumerate(cell_types):
        betas = {c: 0.05 for c in cell_types}
        betas[ct] = 0.95
        markers.append(
            {
                "probe_id": f"cg{i:08d}",
                "chrom": "1",
                "pos": 1000 + i * 10,
                "context": "CG",
                "betas": betas,
            }
        )
    # Extra shared markers
    for j in range(6, 20):
        markers.append(
            {
                "probe_id": f"cg{j:08d}",
                "chrom": "1",
                "pos": 1000 + j * 10,
                "context": "CG",
                "betas": {c: 0.2 + 0.1 * (k % 3) for k, c in enumerate(cell_types)},
            }
        )
    path = tmp_path / "tiny_basis.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_package": "test",
                "cell_types": cell_types,
                "n_markers": len(markers),
                "markers": markers,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_deconvolve_sample_from_h5(tmp_path: Path):
    basis_path = _tiny_basis(tmp_path)
    from methyl_deconv.core.houseman import load_seed_basis

    basis = load_seed_basis(basis_path)
    true = np.array([0.05, 0.10, 0.10, 0.15, 0.20, 0.40], dtype=np.float64)
    y = basis.M @ true
    sample_dir = tmp_path / "S1"
    _write_sample_h5(sample_dir / "1-CG.h5", basis.positions.astype(np.uint32), y)

    row = deconvolve_sample(
        sample_dir,
        basis,
        CellDeconvRuntimeParams(
            contexts=["CG"],
            marker_min_coverage=1,
            min_marker_fraction=0.5,
            use_gpu=False,
        ),
    )
    assert row["qp_status"] == "ok"
    est = np.array([row[ct] for ct in basis.cell_types], dtype=np.float64)
    assert abs(float(np.sum(est)) - 1.0) < 1e-5
    assert float(np.max(np.abs(est - true))) < 0.12


def test_runner_requires_operator_thresholds(tmp_path: Path):
    basis_path = _tiny_basis(tmp_path)
    cfg = CellDeconvStepConfig(seed_basis_path=str(basis_path), use_gpu=False)
    with pytest.raises(ValueError, match="missing contexts, marker_min_coverage, min_marker_fraction"):
        run_cell_deconv_for_samples([("S", str(tmp_path), "healthy")], tmp_path / "out", cfg)


def test_runner_writes_csv(tmp_path: Path):
    basis_path = _tiny_basis(tmp_path)
    from methyl_deconv.core.houseman import load_seed_basis

    basis = load_seed_basis(basis_path)
    true = np.full(6, 1.0 / 6.0)
    y = basis.M @ true
    sample_dir = tmp_path / "S2"
    _write_sample_h5(sample_dir / "1-CG.h5", basis.positions.astype(np.uint32), y)
    out = tmp_path / "out"
    cfg = CellDeconvStepConfig(
        seed_basis_path=str(basis_path),
        contexts=["CG"],
        marker_min_coverage=1,
        min_marker_fraction=0.5,
        use_gpu=False,
    )
    summary = run_cell_deconv_for_samples([("S2", str(sample_dir), "healthy")], out, cfg)
    assert summary["n_samples"] == 1
    assert summary["n_ok"] == 1
    csv_path = Path(summary["output_csv"])
    assert csv_path.is_file()
    df = pd.read_csv(csv_path)
    assert list(df.columns) == [
        "sample_id",
        "group",
        "CD8T",
        "CD4T",
        "NK",
        "Bcell",
        "Mono",
        "Neu",
        "n_markers_observed",
        "marker_fraction",
        "qp_status",
    ]
    assert df.loc[0, "sample_id"] == "S2"
    assert df.loc[0, "group"] == "healthy"
    assert summary["n_columns"] == 11
