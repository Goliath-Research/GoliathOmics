"""Tests for in-process biomarker gene pool (PPI-only stability path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from methyl_validation.biomarker_gene_pool import (
    apply_region_hits_filter,
    build_biomarker_gene_pool,
    compute_biomarker_stability_diagnostics,
    normalize_region_hits,
)


def _sample_mapper_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "gene_name": ["TP53", "BRCA1", "MYC", "GAPDH"],
            "gene_importance": [10.0, 8.0, 6.0, 1.0],
            "mean_effect_size": [0.5, 0.4, 0.3, 0.05],
            "unique_dmps": [5, 4, 3, 1],
            "disease_associated": [True, True, False, False],
            "hits_promoter": [2, 0, 1, 0],
            "hits_exon": [1, 2, 0, 0],
        }
    )


def test_normalize_region_hits_aliases_body():
    assert normalize_region_hits(["body", "promoter"]) == ["gene_body", "promoter"]


def test_apply_region_hits_filter_promoter_or_exon():
    df = _sample_mapper_df()
    out = apply_region_hits_filter(df, ["promoter"])
    assert set(out["gene_name"]) == {"TP53", "MYC"}


def test_build_biomarker_gene_pool_disease_only():
    enricher = {"disease_only": True, "min_dmp_count": 2}
    genes, hubs, meta = build_biomarker_gene_pool(
        _sample_mapper_df(),
        enricher_config=enricher,
        mode="disease_only",
        top_genes=10,
    )
    assert set(genes) == {"TP53", "BRCA1"}
    assert hubs.empty
    assert meta["n_after_csv_filters"] == 2
    assert meta.get("ppi_fetch_attempted") is False


def test_build_biomarker_gene_pool_ppi_only_mocked():
    enricher = {
        "disease_only": True,
        "min_dmp_count": 2,
        "network_refinement": {"score_threshold": 400.0, "hub_ranking_mode": "signal_weighted"},
    }

    edges = pd.DataFrame(
        {
            "source": ["TP53", "BRCA1"],
            "target": ["BRCA1", "TP53"],
            "score": [500.0, 500.0],
        }
    )
    node_metrics = pd.DataFrame(
        {
            "gene": ["TP53", "BRCA1"],
            "degree": [1, 1],
            "degree_centrality": [0.5, 0.5],
            "betweenness_centrality": [0.1, 0.1],
            "closeness_centrality": [0.2, 0.2],
        }
    )

    with patch("methyl_enricher.ppi_network.fetch_string_edges", return_value=edges), patch(
        "methyl_enricher.ppi_network.compute_network_metrics", return_value=node_metrics
    ):
        genes, hubs, meta = build_biomarker_gene_pool(
            _sample_mapper_df(),
            enricher_config=enricher,
            mode="ppi_only",
            top_genes=10,
            ppi_top_hubs=2,
            cache_path="/tmp/string_cache",
        )

    assert genes == ["TP53", "BRCA1"] or genes == ["BRCA1", "TP53"]
    assert not hubs.empty
    assert meta.get("ppi_fetch_attempted") is True
    assert meta.get("n_ppi_hubs", 0) >= 1


def test_build_biomarker_gene_pool_honors_legacy_unique_dmps_config_key():
    enricher = {"disease_only": False, "unique_dmps": 4}
    genes, _, meta = build_biomarker_gene_pool(
        _sample_mapper_df(),
        enricher_config=enricher,
        mode="disease_only",
        top_genes=10,
    )
    assert set(genes) == {"TP53", "BRCA1"}
    assert meta["n_after_csv_filters"] == 2


def test_build_biomarker_gene_pool_ppi_min_degree_zero_keeps_isolated_nodes():
    enricher = {
        "disease_only": True,
        "min_dmp_count": 2,
        "network_refinement": {"score_threshold": 400.0},
    }
    edges = pd.DataFrame(columns=["source", "target", "score"])
    node_metrics = pd.DataFrame(
        {
            "gene": ["TP53", "BRCA1"],
            "degree": [0, 0],
            "degree_centrality": [0.0, 0.0],
            "betweenness_centrality": [0.0, 0.0],
            "closeness_centrality": [0.0, 0.0],
        }
    )

    with patch("methyl_enricher.ppi_network.fetch_string_edges", return_value=edges), patch(
        "methyl_enricher.ppi_network.compute_network_metrics", return_value=node_metrics
    ):
        genes, _, meta = build_biomarker_gene_pool(
            _sample_mapper_df(),
            enricher_config=enricher,
            mode="ppi_only",
            top_genes=10,
            ppi_top_hubs=2,
            min_degree=0,
            cache_path="/tmp/string_cache",
        )

    assert set(genes) == {"TP53", "BRCA1"}
    assert meta.get("ppi_skip_reason") != "all_nodes_below_min_degree"


def test_compute_biomarker_stability_diagnostics(tmp_path: Path):
    run_dir = tmp_path / "run_0001" / "gene_stability"
    run_dir.mkdir(parents=True)
    import json

    with open(run_dir / "gene_featurecuts_metrics.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "biomarker_filter": {
                    "enabled": True,
                    "biomarker_pool_size": 42,
                }
            },
            f,
        )

    diag = compute_biomarker_stability_diagnostics(tmp_path)
    assert diag["enabled"] is True
    assert diag["runs_with_biomarker_filter"] == 1
    assert diag["median_pool_size"] == 42.0


def test_validate_biomarker_allows_mapper_only_without_gene_featurecuts(monkeypatch):
    from methyl_validation.config import MonteCarloConfig
    from methyl_validation.mc_config_load import validate_stability_gene_biomarker_config

    cfg = MonteCarloConfig.model_validate(
        {
            "samples_base_path": "/work/samples",
            "base_project": "/tmp/project.json",
            "output_base": "/work/out",
            "cohorts": [
                {"label": "healthy", "csv": "/a.csv"},
                {"label": "PCa", "csv": "/b.csv"},
            ],
            "train_fraction": 0.8,
            "n_iterations": 2,
            "stability_gene_biomarker_filter_enabled": True,
            "stability_gene_featurecuts_enabled": False,
        }
    )
    validate_stability_gene_biomarker_config(cfg)
