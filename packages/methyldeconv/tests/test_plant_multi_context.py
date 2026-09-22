"""Plant / multi-context deconvolution seams (no blood fallback)."""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from methyl_deconv.config import CellDeconvRuntimeParams, CellDeconvStepConfig
from methyl_deconv.core.houseman import extract_marker_vector, load_seed_basis
from methyl_deconv.core.hitimed import load_hierarchy_basis
from methyl_deconv.core.runner import run_cell_deconv_for_samples


def _write_h5(path: Path, positions: np.ndarray, betas: np.ndarray, cov: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mC = np.round(betas * cov).astype(np.uint32)
    uC = (cov - mC).astype(np.uint32)
    with h5py.File(path, "w") as f:
        g = f.create_group("methylation_data")
        g.create_dataset("pos", data=positions.astype(np.uint32))
        g.create_dataset("mC", data=mC)
        g.create_dataset("uC", data=uC)
        g.create_dataset("tnc", data=np.zeros(len(positions), dtype=np.uint8))


def _plant_houseman_basis(tmp_path: Path) -> Path:
    markers = [
        {
            "probe_id": "cg_cg_0",
            "chrom": "1",
            "pos": 1000,
            "context": "CG",
            "betas": {"mesophyll": 0.9, "vasculature": 0.1},
        },
        {
            "probe_id": "cg_chg_0",
            "chrom": "1",
            "pos": 2000,
            "context": "CHG",
            "betas": {"mesophyll": 0.2, "vasculature": 0.8},
        },
        {
            "probe_id": "cg_chh_0",
            "chrom": "1",
            "pos": 3000,
            "context": "CHH",
            "betas": {"mesophyll": 0.5, "vasculature": 0.5},
        },
    ]
    path = tmp_path / "plant_seed.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cell_types": ["mesophyll", "vasculature"],
                "markers": markers,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_multi_context_extract_reads_cg_chg_chh(tmp_path: Path) -> None:
    basis_path = _plant_houseman_basis(tmp_path)
    basis = load_seed_basis(basis_path)
    sample_dir = tmp_path / "S"
    _write_h5(sample_dir / "1-CG.h5", np.array([1000], dtype=np.uint32), np.array([0.91]))
    _write_h5(sample_dir / "1-CHG.h5", np.array([2000], dtype=np.uint32), np.array([0.82]))
    _write_h5(sample_dir / "1-CHH.h5", np.array([3000], dtype=np.uint32), np.array([0.55]))

    y, observed = extract_marker_vector(
        sample_dir,
        basis,
        CellDeconvRuntimeParams(
            contexts=["CG", "CHG", "CHH"],
            marker_min_coverage=1,
            min_marker_fraction=0.5,
            use_gpu=False,
        ),
    )
    assert observed.all()
    assert abs(float(y[0]) - 0.91) < 0.05
    assert abs(float(y[1]) - 0.82) < 0.05
    assert abs(float(y[2]) - 0.55) < 0.05


def test_plant_tissue_requires_seed_basis_path(tmp_path: Path) -> None:
    cfg = CellDeconvStepConfig(
        method="houseman",
        analyte="plant_tissue",
        contexts=["CG", "CHG", "CHH"],
        marker_min_coverage=1,
        min_marker_fraction=0.5,
        use_gpu=False,
    )
    with pytest.raises(ValueError, match="seed_basis_path"):
        run_cell_deconv_for_samples([("S", str(tmp_path), "control")], tmp_path / "out", cfg)


def test_plant_tissue_requires_hierarchy_basis_path(tmp_path: Path) -> None:
    cfg = CellDeconvStepConfig(
        method="hitimed",
        analyte="leaf",
        contexts=["CG"],
        marker_min_coverage=1,
        min_marker_fraction=0.5,
        use_gpu=False,
    )
    with pytest.raises(ValueError, match="hierarchy_basis_path"):
        run_cell_deconv_for_samples([("S", str(tmp_path), "control")], tmp_path / "out", cfg)


def test_hitimed_plant_tissue_root_from_path_json(tmp_path: Path) -> None:
    basis = {
        "schema_version": 2,
        "analyte_trees": {"plant_tissue": "plant_root", "default": "plant_root"},
        "nodes": {
            "plant_root": {
                "children": ["mesophyll", "vasculature"],
                "markers": [
                    {
                        "probe_id": "p0",
                        "chrom": "1",
                        "pos": 100,
                        "context": "CG",
                        "betas": {"mesophyll": 0.9, "vasculature": 0.1},
                    },
                    {
                        "probe_id": "p1",
                        "chrom": "1",
                        "pos": 110,
                        "context": "CHG",
                        "betas": {"mesophyll": 0.1, "vasculature": 0.9},
                    },
                ],
            }
        },
    }
    path = tmp_path / "plant_hitimed.json"
    path.write_text(json.dumps(basis), encoding="utf-8")
    loaded = load_hierarchy_basis(path)
    assert loaded.root_for_analyte("plant_tissue") == "plant_root"
    assert loaded.root_for_analyte("leaf") == "plant_root"
    assert loaded.nodes["plant_root"].contexts == ("CG", "CHG")
