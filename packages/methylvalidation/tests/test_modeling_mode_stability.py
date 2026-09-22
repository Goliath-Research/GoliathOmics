"""Tests for mapper gene stability and stable DMP panel loading."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from methyl_gene_select.core.dmp_panel import (
    load_selected_dmp_panel,
    load_stable_dmp_panel,
)
from methyl_validation.stability import compute_gene_stability, load_mapper_genes


def _write_mapper_genes(run_dir: Path, genes: list[str]) -> None:
    mapper_dir = run_dir / "mapper" / "healthy" / "pca"
    mapper_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"gene_name": genes, "gene_importance": [1.0] * len(genes)}).to_csv(
        mapper_dir / "all-gene_name-combined.csv",
        index=False,
    )


def test_load_mapper_genes_from_combined_csv(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_001"
    _write_mapper_genes(run_dir, ["BRCA1", "TP53"])
    df = load_mapper_genes(run_dir)
    assert df is not None
    assert set(df["gene_name"]) == {"BRCA1", "TP53"}


def test_compute_gene_stability_mapper_axis(tmp_path: Path) -> None:
    mc_root = tmp_path / "monte_carlo_runs"
    for i, genes in enumerate([["BRCA1", "TP53"], ["BRCA1", "MYC"], ["TP53", "MYC"]]):
        _write_mapper_genes(mc_root / f"run_{i:03d}", genes)
    df, summary = compute_gene_stability(
        mc_root,
        min_frequency=0.5,
        prefer_mapper_gene_panels=True,
        gene_recurrence_source="mapper",
    )
    assert summary["gene_recurrence_source"] == "mapper"
    assert summary["n_runs_analyzed"] == 3
    assert "BRCA1" in set(df["gene_name"])


def test_load_selected_dmp_panel_prefers_selected_csv(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_001"
    det = run_dir / "detections" / "healthy" / "pca"
    det.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"chromosome": [1], "position": [100], "effect_size": [0.5]}).to_csv(
        det / "dmps-1-selected.csv", index=False
    )
    pd.DataFrame({"chromosome": [1], "position": [200], "effect_size": [0.1]}).to_csv(
        det / "dmps-1-classifier.csv", index=False
    )
    df = load_selected_dmp_panel(run_dir)
    assert df is not None
    assert int(df.iloc[0]["position"]) == 100


def test_load_stable_dmp_panel_from_explicit_csv(tmp_path: Path) -> None:
    stable = tmp_path / "stable_dmps_production.csv"
    pd.DataFrame({"chromosome": [1, 2], "position": [10, 20], "frequency": [0.9, 0.85]}).to_csv(
        stable, index=False
    )
    df = load_stable_dmp_panel(tmp_path / "run_001", stable_csv=stable)
    assert df is not None
    assert len(df) == 2
