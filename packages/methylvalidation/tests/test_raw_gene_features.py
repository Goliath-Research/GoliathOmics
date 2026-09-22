from __future__ import annotations

import numpy as np
import pandas as pd

from methyl_validation.raw_gene_features import (
    build_gene_locus_index_map,
    build_gene_panel_feature_weights,
    build_raw_gene_feature_table,
    resolve_stable_gene_names,
)
from methyl_validation.stability import _normalize_production_ecdf_backend


def test_resolve_stable_gene_names_deduplicates_and_sorts():
    panel = pd.DataFrame(
        {
            "comparison_label": ["A", "A", "B"],
            "gene_name": ["BRCA1", "TP53", "BRCA1"],
            "gene_support_n": [2, 3, 2],
            "gene_importance": [0.9, 0.8, 0.7],
        }
    )
    assert resolve_stable_gene_names(panel) == ["BRCA1", "TP53"]


def test_build_gene_locus_index_map_links_loci_to_genes():
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp"] * 3,
            "chromosome": ["1", "1", "2"],
            "context": ["CG"] * 3,
            "position": [100, 200, 300],
            "effect_size": [0.5, -0.3, 0.2],
            "region_weight": [1.0, 2.0, 1.0],
            "gene_name": ["GENE1", "GENE1", "GENE2"],
        }
    )
    order = [("1", "CG", 100), ("1", "CG", 200), ("2", "CG", 300)]
    gene_map, locus_w = build_gene_locus_index_map(
        dmp_df,
        order,
        ["GENE1", "GENE2"],
        use_region_weight=True,
    )
    assert gene_map["GENE1"] == [0, 1]
    assert gene_map["GENE2"] == [2]
    assert locus_w[1] > locus_w[0]


def test_build_gene_panel_feature_weights_uses_gene_importance():
    panel = pd.DataFrame(
        {
            "comparison_label": ["A", "B"],
            "gene_name": ["GENE1", "GENE1"],
            "gene_importance": [0.4, 0.2],
            "gene_support_n": [2, 2],
        }
    )
    weights = build_gene_panel_feature_weights(
        panel,
        ["gene::GENE1", "gene::GENE2"],
        weight_column="gene_importance",
    )
    assert weights.shape == (2,)
    assert weights[0] == 1.0
    assert weights[1] > 0.0


def test_build_raw_gene_feature_table_weighted_aggregate(monkeypatch):
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp"] * 2,
            "chromosome": ["1", "1"],
            "context": ["CG", "CG"],
            "position": [100, 200],
            "effect_size": [1.0, 1.0],
            "region_weight": [1.0, 1.0],
            "gene_name": ["GENE1", "GENE1"],
        }
    )
    panel = pd.DataFrame(
        {
            "comparison_label": ["cmp"],
            "gene_name": ["GENE1"],
            "gene_support_n": [2],
            "gene_importance": [1.0],
        }
    )

    def _fake_extract(sample_paths, refs, feature_order, min_coverage=1):
        del sample_paths, refs, feature_order, min_coverage
        return np.asarray([[0.2, 0.8]], dtype=np.float32)

    monkeypatch.setattr(
        "methyl_gene_select.core.sample_matrix._extract_matrix_for_samples",
        _fake_extract,
    )
    feat = build_raw_gene_feature_table(
        ["/tmp/sample.h5"],
        dmp_df,
        panel,
        min_coverage=1,
    )
    assert feat.feature_names == ["gene::GENE1"]
    assert feat.X.shape == (1, 1)
    assert np.isclose(float(feat.X[0, 0]), 0.5)


def test_normalize_production_ecdf_backend_defaults_to_raw_dmp():
    project = {
        "actionConfig": {
            "validation": {
                "backend_profiles": {
                    "ecdf": {
                        "params": {
                            "feature_mode": "observed_hybrid",
                            "feature_family_set": "gene_scored",
                        }
                    }
                }
            }
        }
    }
    _normalize_production_ecdf_backend(project)
    params = project["actionConfig"]["validation"]["backend_profiles"]["ecdf"]["params"]
    assert params["feature_mode"] == "raw_dmp"
    assert params["feature_family_set"] == "dmp_scored"
    assert params["model_weight_column"] == "effect_size"
    assert params["ecdf_aggregated_enabled"] is False


def test_normalize_production_ecdf_backend_preserves_raw_gene():
    project = {
        "actionConfig": {
            "validation": {
                "backend_profiles": {
                    "ecdf": {
                        "params": {
                            "feature_mode": "raw_gene",
                            "feature_family_set": "gene_scored",
                        }
                    }
                }
            }
        }
    }
    _normalize_production_ecdf_backend(project)
    params = project["actionConfig"]["validation"]["backend_profiles"]["ecdf"]["params"]
    assert params["feature_mode"] == "raw_gene"
    assert params["feature_family_set"] == "gene_scored"
