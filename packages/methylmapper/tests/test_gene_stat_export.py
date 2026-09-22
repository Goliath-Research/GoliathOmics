from __future__ import annotations

import pandas as pd
import pytest

from methyl_mapper.bedtools_mapper import BedtoolsMapper
from methyl_mapper.config import BiologyWeightConfig


def _mapper(**kwargs) -> BedtoolsMapper:
    mapper = BedtoolsMapper.__new__(BedtoolsMapper)
    mapper.storey_lambda = None
    mapper.biology_weights = kwargs.pop("biology_weights", BiologyWeightConfig())
    mapper._explicit_feature_labels = kwargs.pop("_explicit_feature_labels", set())
    return mapper


def test_aggregate_by_feature_exports_gene_p_and_q_columns_with_q_only_input():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G1", "G1", "G2", "G2"],
            "dmp_name": ["d1", "d2", "d3", "d4"],
            "feature_type": ["promoter", "promoter", "promoter", "promoter"],
            "weight": [1.0, 2.0, 1.5, 2.5],
            "q_value": [0.01, 0.02, 0.05, 0.10],
            "delta_mean": [0.2, -0.3, 0.1, -0.2],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    assert "gene_p_value" in grouped.columns
    assert "gene_q_value" in grouped.columns
    assert grouped["gene_p_value"].notna().any()


def test_biology_matrix_weights_promoter_hyper_above_intron_hypo():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G1", "G1"],
            "dmp_name": ["d1", "d2"],
            "feature_type": ["promoter", "intron"],
            "effect_size": [0.4, 0.4],
            "delta_mean": [0.4, -0.4],
            "frequency": [1.0, 1.0],
        }
    )
    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    row = grouped.iloc[0]
    # promoter hyper: 2.0 * 0.4 = 0.8; intron hypo: 0.7 * 0.4 = 0.28
    assert row["feature_importance_promoter"] == pytest.approx(0.8)
    assert row["feature_importance_intron"] == pytest.approx(0.28)
    assert row["feature_importance_promoter"] > row["feature_importance_intron"]


def test_aggregate_by_feature_exports_biology_weighted_gene_importance():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G1", "G1", "G1", "G2"],
            "dmp_name": ["d1", "d2", "d3", "d4"],
            "feature_type": ["exon", "exon", "promoter", "intron"],
            "feature_start": [10, 10, 1, 20],
            "feature_end": [20, 20, 5, 30],
            "weight": [1.0, 1.0, 1.0, 1.0],
            "effect_size": [0.5, -0.2, 0.4, -0.3],
            "delta_mean": [0.5, -0.2, 0.4, -0.3],
            "frequency": [1.0, 1.0, 1.0, 1.0],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    row_g1 = grouped[grouped["gene_name"] == "G1"].iloc[0]
    # exon hyper 1.5*0.5 + exon hypo 1.0*0.2; promoter hyper 2.0*0.4
    assert row_g1["gene_effect_abs_wsum"] == pytest.approx(0.75 + 0.2 + 0.8)
    assert row_g1["gene_direction"] == pytest.approx(1.0)
    assert row_g1["gene_importance"] == pytest.approx(
        row_g1["gene_effect_abs_wsum"] * row_g1["gene_direction_coherence"] * (row_g1["gene_support_freq"] ** 0.5)
    )
    assert row_g1["feature_direction_promoter"] == pytest.approx(1.0)
    assert row_g1["feature_effect_signed_wsum_promoter"] == pytest.approx(0.8)


def test_aggregate_by_feature_uses_exclusive_feature_priority_for_hits():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G1", "G1", "G1"],
            "dmp_name": ["d1", "d1", "d2"],
            "feature_type": ["gene_body", "exon", "intron"],
            "weight": [1.0, 1.0, 1.0],
            "effect_size": [0.5, 0.5, 0.4],
            "delta_mean": [0.5, 0.5, 0.4],
            "frequency": [0.8, 0.8, 0.9],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    row = grouped.iloc[0]
    assert row["hits_exon"] == 1
    assert row["hits_intron"] == 1
    assert row["hits_gene_body"] == 0
    assert "gene_score" not in grouped.columns


def test_aggregate_by_feature_sorts_by_canonical_gene_importance_desc():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G_low", "G_high"],
            "dmp_name": ["d1", "d2"],
            "feature_type": ["promoter", "promoter"],
            "feature_start": [1, 1],
            "feature_end": [10, 10],
            "weight": [1.0, 1.0],
            "effect_size": [0.1, 0.4],
            "delta_mean": [0.1, 0.4],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    assert grouped.iloc[0]["gene_name"] == "G_high"
    assert grouped.iloc[0]["gene_importance"] > grouped.iloc[1]["gene_importance"]


def test_aggregate_by_feature_gene_importance_scales_with_unique_dmp_support():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G_many", "G_many", "G_many", "G_one"],
            "dmp_name": ["d1", "d2", "d3", "d4"],
            "feature_type": ["promoter", "promoter", "promoter", "promoter"],
            "effect_size": [0.2, 0.2, 0.2, 0.2],
            "delta_mean": [0.2, 0.2, 0.2, 0.2],
            "frequency": [1.0, 1.0, 1.0, 1.0],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    row_many = grouped[grouped["gene_name"] == "G_many"].iloc[0]
    row_one = grouped[grouped["gene_name"] == "G_one"].iloc[0]

    assert row_many["gene_support_n"] == 3
    assert row_one["gene_support_n"] == 1
    assert row_many["gene_effect_abs_wsum"] > row_one["gene_effect_abs_wsum"]
    assert row_many["gene_importance"] > row_one["gene_importance"]


def test_aggregate_by_feature_exports_compound_columns():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G1", "G1", "G1"],
            "dmp_name": ["d1", "d2", "d3"],
            "feature_type": ["promoter", "exon", "intron"],
            "feature_start": [1, 10, 30],
            "feature_end": [5, 20, 40],
            "weight": [1.0, 1.0, 1.0],
            "effect_size": [0.6, 0.2, 0.1],
            "delta_mean": [0.6, 0.2, 0.1],
            "frequency": [1.0, 0.8, 0.9],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    row = grouped.iloc[0]
    assert "gene_effect_abs_wmean" in grouped.columns
    assert "gene_importance" in grouped.columns
    assert "feature_importance_promoter" in grouped.columns
    assert "feature_direction_promoter" in grouped.columns
    assert row["gene_support_n"] == 3
    assert row["gene_effect_compound"] > 0.0
    assert "mean_effect_size" not in grouped.columns
    assert "gene_feature_effect_compound" not in grouped.columns


def test_aggregate_by_feature_exports_signed_directional_biomarker_columns():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G_mixed", "G_mixed", "G_pos"],
            "dmp_name": ["d1", "d2", "d3"],
            "feature_type": ["promoter", "promoter", "intron"],
            "effect_size": [0.5, -0.4, 0.3],
            "delta_mean": [0.5, -0.4, 0.3],
            "frequency": [1.0, 1.0, 1.0],
            "weight": [1.0, 1.0, 1.0],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    row_mixed = grouped[grouped["gene_name"] == "G_mixed"].iloc[0]
    row_pos = grouped[grouped["gene_name"] == "G_pos"].iloc[0]

    assert row_mixed["gene_effect_signed_wsum"] == pytest.approx(0.5 * 2.0 - 0.4 * 1.0)
    assert row_mixed["feature_effect_signed_wsum_promoter"] == pytest.approx(0.5 * 2.0 - 0.4 * 1.0)
    assert row_pos["feature_effect_signed_wsum_intron"] == pytest.approx(0.3 * 0.5)


def test_aggregate_by_feature_rejects_invalid_stability_frequency_values():
    mapper = _mapper()
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G1"],
            "dmp_name": ["d1"],
            "feature_type": ["promoter"],
            "weight": [1.0],
            "effect_size": [0.5],
            "frequency": [1.2],
            "count": [12],
            "n_runs": [10],
        }
    )

    with pytest.raises(ValueError, match="Invalid stability frequency values"):
        BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")


def test_aggregate_by_feature_rolls_up_detailed_features_to_parent_buckets():
    mapper = _mapper(_explicit_feature_labels=set())
    intersect_df = pd.DataFrame(
        {
            "gene_name": ["G1", "G1"],
            "dmp_name": ["d1", "d2"],
            "feature_type": ["CDS", "five_prime_UTR"],
            "feature_start": [10, 30],
            "feature_end": [20, 40],
            "weight": [1.0, 1.0],
            "effect_size": [0.6, 0.2],
            "delta_mean": [0.6, 0.2],
            "frequency": [1.0, 1.0],
        }
    )

    grouped = BedtoolsMapper.aggregate_by_feature(mapper, intersect_df, group_by="gene_name")
    row = grouped.iloc[0]
    assert row["hits_exon"] == 2
    assert row["feature_importance_exon"] == pytest.approx((0.6 * 1.5) + (0.2 * 1.5))


def test_pruned_gene_output_drops_legacy_columns():
    df = pd.DataFrame(
        {
            "gene_name": ["G1"],
            "unique_dmps": [2],
            "total_weight": [3.0],
            "mean_effect_size": [0.2],
            "gene_effect_size": [0.1],
            "gene_score": [0.4],
            "gene_effect_compound": [0.3],
            "gene_importance": [0.5],
        }
    )
    pruned = BedtoolsMapper._prune_gene_output_columns(df)
    assert "total_weight" not in pruned.columns
    assert "mean_effect_size" not in pruned.columns
    assert "gene_score" not in pruned.columns
    assert "gene_importance" in pruned.columns
    assert "gene_effect_compound" in pruned.columns
