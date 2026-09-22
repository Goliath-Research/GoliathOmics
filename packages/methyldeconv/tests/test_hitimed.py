"""Unit + integration tests for hierarchical (HiTIMED-style) deconvolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import h5py
import numpy as np
import pandas as pd
import pytest

from methyl_deconv.config import CellDeconvRuntimeParams, CellDeconvStepConfig
from methyl_deconv.core.hitimed import (
    hitimed_deconvolve,
    load_hierarchy_basis,
)
from methyl_deconv.core.runner import run_cell_deconv_for_samples


# --- synthetic hierarchy: plasma -> (tumor_fraction, immune); immune -> (CD4, CD8) ---

def _two_child_markers(pid_prefix: str, base_pos: int, child_a: str, child_b: str) -> List[dict]:
    """Identity-ish 2-child markers (recoverable by QP) plus a shared row."""
    rows = [
        {child_a: 0.9, child_b: 0.1},
        {child_a: 0.1, child_b: 0.9},
        {child_a: 0.7, child_b: 0.3},
        {child_a: 0.3, child_b: 0.7},
    ]
    markers = []
    for i, betas in enumerate(rows):
        markers.append(
            {
                "probe_id": f"{pid_prefix}{i:04d}",
                "chrom": "1",
                "pos": base_pos + i * 10,
                "context": "CG",
                "betas": betas,
            }
        )
    return markers


def _write_basis(tmp_path: Path) -> Path:
    asset = {
        "schema_version": 2,
        "basis_kind": "hierarchy",
        "genome_build": "hg38",
        "coordinate_convention": "1-based",
        "analyte_trees": {"cfdna": "plasma", "buffy_coat": "immune"},
        "nodes": {
            "plasma": {
                "children": ["tumor_fraction", "immune"],
                "markers": _two_child_markers("p", 1000, "tumor_fraction", "immune"),
            },
            "immune": {
                "children": ["CD4", "CD8"],
                "markers": _two_child_markers("i", 5000, "CD4", "CD8"),
            },
        },
    }
    p = tmp_path / "hierarchy.json"
    p.write_text(json.dumps(asset), encoding="utf-8")
    return p


def _runtime(**kw) -> CellDeconvRuntimeParams:
    base = dict(
        method="hitimed",
        contexts=["CG"],
        marker_min_coverage=1,
        min_marker_fraction=0.25,
        use_gpu=False,
    )
    base.update(kw)
    return CellDeconvRuntimeParams(**base)


def _beta_map_for(basis, node_id: str, weights: Dict[str, float]) -> Dict[str, float]:
    node = basis.nodes[node_id]
    w = np.asarray([weights[c] for c in node.children], dtype=np.float64)
    y = node.M @ w
    return {pid: float(v) for pid, v in zip(node.probe_ids, y)}


def test_analyte_selects_root_and_leaves(tmp_path: Path):
    basis = load_hierarchy_basis(_write_basis(tmp_path))
    assert basis.root_for_analyte("cfdna") == "plasma"
    assert basis.root_for_analyte("buffy_coat") == "immune"
    # cfDNA exposes lumped tumor plus immune leaves; buffy coat has no tumor.
    assert basis.leaf_order("plasma") == ["tumor_fraction", "CD4", "CD8"]
    assert basis.leaf_order("immune") == ["CD4", "CD8"]


def test_tree_recovery_cfdna(tmp_path: Path):
    basis = load_hierarchy_basis(_write_basis(tmp_path))
    beta_map: Dict[str, float] = {}
    beta_map.update(_beta_map_for(basis, "plasma", {"tumor_fraction": 0.1, "immune": 0.9}))
    beta_map.update(_beta_map_for(basis, "immune", {"CD4": 0.6, "CD8": 0.4}))

    row = hitimed_deconvolve(beta_map, basis, "plasma", _runtime())
    assert row["qp_status"] == "ok"
    assert pytest.approx(row["tumor_fraction"], abs=0.02) == 0.1
    assert pytest.approx(row["CD4"], abs=0.03) == 0.54  # 0.9 * 0.6
    assert pytest.approx(row["CD8"], abs=0.03) == 0.36  # 0.9 * 0.4
    assert pytest.approx(row["tumor_fraction"] + row["CD4"] + row["CD8"], abs=1e-6) == 1.0


def test_buffy_coat_has_no_tumor_leaf(tmp_path: Path):
    basis = load_hierarchy_basis(_write_basis(tmp_path))
    beta_map = _beta_map_for(basis, "immune", {"CD4": 0.7, "CD8": 0.3})
    row = hitimed_deconvolve(beta_map, basis, "immune", _runtime(analyte="buffy_coat"))
    assert row["qp_status"] == "ok"
    assert "tumor_fraction" not in row
    assert pytest.approx(row["CD4"] + row["CD8"], abs=1e-6) == 1.0
    assert pytest.approx(row["CD4"], abs=0.03) == 0.7


def test_low_tumor_fraction_recovered(tmp_path: Path):
    basis = load_hierarchy_basis(_write_basis(tmp_path))
    beta_map: Dict[str, float] = {}
    beta_map.update(_beta_map_for(basis, "plasma", {"tumor_fraction": 0.02, "immune": 0.98}))
    beta_map.update(_beta_map_for(basis, "immune", {"CD4": 0.5, "CD8": 0.5}))
    row = hitimed_deconvolve(beta_map, basis, "plasma", _runtime())
    assert row["tumor_fraction"] < 0.08
    assert row["CD4"] > 0.4 and row["CD8"] > 0.4


def test_partial_when_child_node_unobserved(tmp_path: Path):
    basis = load_hierarchy_basis(_write_basis(tmp_path))
    # Observe only the plasma split; immune markers missing -> even split fallback.
    beta_map = _beta_map_for(basis, "plasma", {"tumor_fraction": 0.2, "immune": 0.8})
    row = hitimed_deconvolve(beta_map, basis, "plasma", _runtime())
    assert row["qp_status"] == "partial"
    # immune mass 0.8 split evenly across CD4/CD8 before renormalization
    assert pytest.approx(row["CD4"], abs=0.02) == 0.4
    assert pytest.approx(row["CD8"], abs=0.02) == 0.4


def test_insufficient_when_root_unobserved(tmp_path: Path):
    basis = load_hierarchy_basis(_write_basis(tmp_path))
    row = hitimed_deconvolve({}, basis, "plasma", _runtime())
    assert row["qp_status"] == "insufficient_markers"
    assert np.isnan(row["tumor_fraction"])


def test_packaged_blood_hierarchy_loads():
    from methyl_deconv.core.hitimed import default_hierarchy_basis_path

    basis = load_hierarchy_basis(default_hierarchy_basis_path())
    assert basis.root_for_analyte("buffy_coat") == "immune"
    leaves = basis.leaf_order("immune")
    assert set(leaves) == {"CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu"}


# --- integration through H5 + runner ---

def _write_sample_h5(path: Path, positions: np.ndarray, betas: np.ndarray, cov: int = 20) -> None:
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


def test_runner_hitimed_writes_hierarchical_csv(tmp_path: Path):
    basis_path = _write_basis(tmp_path)
    basis = load_hierarchy_basis(basis_path)

    beta_map: Dict[str, float] = {}
    beta_map.update(_beta_map_for(basis, "plasma", {"tumor_fraction": 0.15, "immune": 0.85}))
    beta_map.update(_beta_map_for(basis, "immune", {"CD4": 0.6, "CD8": 0.4}))

    # Build a single-chrom H5 covering all node probes.
    probe_pos: Dict[str, int] = {}
    for node in basis.nodes.values():
        for pid, pos in zip(node.probe_ids, node.positions):
            probe_pos[pid] = int(pos)
    positions = np.asarray(sorted(probe_pos.values()), dtype=np.uint32)
    pos_to_pid = {int(p): pid for pid, p in probe_pos.items()}
    betas = np.asarray([beta_map[pos_to_pid[int(p)]] for p in positions], dtype=np.float64)

    sample_dir = tmp_path / "S1"
    _write_sample_h5(sample_dir / "1-CG.h5", positions, betas)

    out = tmp_path / "out"
    cfg = CellDeconvStepConfig(
        method="hitimed",
        hierarchy_basis_path=str(basis_path),
        analyte="cfdna",
        contexts=["CG"],
        marker_min_coverage=1,
        min_marker_fraction=0.25,
        use_gpu=False,
    )
    summary = run_cell_deconv_for_samples([("S1", str(sample_dir), "case")], out, cfg)
    assert summary["method"] == "hitimed"
    assert summary["tree_root"] == "plasma"
    assert summary["cell_types"] == ["tumor_fraction", "CD4", "CD8"]

    df = pd.read_csv(Path(summary["output_csv"]))
    assert list(df.columns) == [
        "sample_id",
        "group",
        "tumor_fraction",
        "CD4",
        "CD8",
        "n_markers_observed",
        "marker_fraction",
        "qp_status",
    ]
    assert df.loc[0, "qp_status"] == "ok"
    assert abs(df.loc[0, ["tumor_fraction", "CD4", "CD8"]].sum() - 1.0) < 1e-6
    assert abs(float(df.loc[0, "tumor_fraction"]) - 0.15) < 0.03
