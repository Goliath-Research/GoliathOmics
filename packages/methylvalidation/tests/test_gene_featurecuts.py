from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_validation.gene_featurecuts import (
    _annotate_dmps_with_genes,
    _cap_ranked_gene_pool,
    _rank_gene_pool,
    _search_gene_k,
    run_gene_featurecuts_for_iteration,
)


def test_rank_gene_pool_orders_by_importance_then_support():
    combined = pd.DataFrame(
        {
            "gene_name": ["GENE_B", "GENE_A", "GENE_C"],
            "gene_importance": [0.5, 0.9, 0.9],
            "gene_support_n": [2, 3, 1],
        }
    )
    ranked, panel = _rank_gene_pool(combined)
    assert ranked == ["GENE_A", "GENE_C", "GENE_B"]
    assert len(panel) == 3


def test_cap_ranked_gene_pool_limits_panel():
    combined = pd.DataFrame(
        {
            "gene_name": [f"GENE_{i}" for i in range(5)],
            "gene_importance": [1.0 - i * 0.1 for i in range(5)],
            "gene_support_n": [1] * 5,
        }
    )
    ranked, panel = _rank_gene_pool(combined)
    capped, capped_panel = _cap_ranked_gene_pool(ranked, panel, 2)
    assert capped == ranked[:2]
    assert len(capped_panel) == 2


def test_annotate_dmps_with_genes_parses_dmp_name_and_chr_prefix():
    dmp_df = pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [10577],
            "context": ["CG"],
            "effect_size": [0.1],
            "region_weight": [1.0],
        }
    )
    intersections = pd.DataFrame(
        {
            "dmp_name": ["1:10577:CG:eff=0.094"],
            "feature_chrom": ["chr1"],
            "gene_name": ["TP53"],
            "context": ["CG"],
        }
    )
    annotated = _annotate_dmps_with_genes(dmp_df, intersections)
    assert annotated.iloc[0]["gene_name"] == "TP53"


def test_search_gene_k_respects_min_genes_floor(monkeypatch):
    calls: list[int] = []

    def _fake_eval(*args, **kwargs):
        k = int(kwargs.get("k", args[7]))
        calls.append(k)
        return float(k) / 10.0, {"balanced_accuracy": float(k) / 10.0}

    monkeypatch.setattr("methyl_gene_select.core.gene_featurecuts._evaluate_gene_prefix", _fake_eval)

    n_features = 5
    X = np.random.default_rng(0).random((8, n_features))
    y = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.int32)
    panel = pd.DataFrame({"gene_name": [f"G{i}" for i in range(n_features)], "mean_effect_size": [0.1] * n_features})

    best_k, best_ba, _ = _search_gene_k(
        X_train=X,
        y_train=y,
        X_val=X,
        y_val=y,
        class_names=["healthy", "disease"],
        feature_names=[f"gene::G{i}" for i in range(n_features)],
        gene_panel=panel,
        target_ba=0.25,
        min_genes=4,
        max_k=n_features,
    )
    assert best_k == 4
    assert best_ba == pytest.approx(0.4)
    assert 4 in calls


def test_run_gene_featurecuts_for_iteration_exports_panel(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_0001"
    run_dir.mkdir()
    project_json = run_dir / "project.json"
    project_json.write_text("{}", encoding="utf-8")

    det_dir = run_dir / "detections" / "chr1" / "Healthy_vs_Disease"
    det_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 200],
            "context": ["CG", "CG"],
            "effect_size": [0.5, 0.3],
            "region_weight": [1.0, 1.0],
        }
    ).to_csv(det_dir / "dmps-Healthy_vs_Disease-classifier.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 200],
            "context": ["CG", "CG"],
            "effect_size": [0.5, 0.3],
            "region_weight": [1.0, 1.0],
        }
    ).to_csv(det_dir / "dmps-Healthy_vs_Disease-discovery.csv", index=False)

    mapper_dir = run_dir / "mapper" / "Healthy_vs_Disease"
    mapper_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "gene_name": ["GENE1", "GENE2"],
            "gene_importance": [0.9, 0.5],
            "mean_effect_size": [0.4, 0.2],
            "gene_support_n": [2, 1],
        }
    ).to_csv(mapper_dir / "all-gene_name-combined.csv", index=False)
    pd.DataFrame(
        {
            "gene_name": ["GENE1", "GENE2"],
            "feature_chrom": ["1", "1"],
            "position": [100, 200],
            "context": ["CG", "CG"],
        }
    ).to_csv(mapper_dir / "Healthy_vs_Disease-intersections.csv", index=False)

    class _Cfg:
        stability_target_balanced_accuracy = None
        stability_min_selected_genes = 1
        stability_gene_featurecuts_max_genes = 500
        stability_gene_featurecuts_dmp_source = "discovery"

    monkeypatch.setattr(
        "methyl_gene_select.core.gene_featurecuts._load_train_paths_and_labels",
        lambda _p: (["/tmp/t1", "/tmp/t2"], np.array([0, 1], dtype=np.int32), ["healthy", "disease"]),
    )
    monkeypatch.setattr(
        "methyl_gene_select.core.gene_featurecuts._load_validation_paths_and_labels",
        lambda _p, _c: (["/tmp/v1", "/tmp/v2"], np.array([0, 1], dtype=np.int32)),
    )

    class _Feat:
        def __init__(self, n: int):
            self.X = np.ones((2, n), dtype=np.float64)
            self.feature_names = [f"gene::G{i}" for i in range(n)]

    monkeypatch.setattr(
        "methyl_gene_select.core.gene_featurecuts.build_raw_gene_feature_table",
        lambda *_a, **_k: _Feat(2),
    )
    monkeypatch.setattr(
        "methyl_gene_select.core.gene_featurecuts._search_gene_k",
        lambda **_k: (2, 0.95, {"balanced_accuracy": 0.95}),
    )

    rc, msg, err = run_gene_featurecuts_for_iteration(project_json, _Cfg())
    assert rc == 0, err
    assert (run_dir / "gene_stability" / "genes-classifier.csv").is_file()
    assert (run_dir / "gene_stability" / "gene_featurecuts_metrics.json").is_file()
    assert "k=2" in msg
