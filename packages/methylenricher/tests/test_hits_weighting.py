from __future__ import annotations

import pandas as pd
import pytest

from methyl_enricher.enricher import EnrichmentAnalyzer


def test_gene_importance_is_used_as_canonical_weight():
    analyzer = EnrichmentAnalyzer(libraries=["GO_Biological_Process_2023"])
    df = pd.DataFrame(
        {
            "gene_name": ["G1"],
            "gene_importance": [42.5],
        }
    )
    out = analyzer._apply_csv_filters(df)
    row = out.iloc[0]
    assert analyzer._gene_weight_from_row(row) == 42.5


def test_hits_filter_removes_rows_without_any_hits():
    analyzer = EnrichmentAnalyzer(libraries=["GO_Biological_Process_2023"])
    df = pd.DataFrame(
        {
            "gene_name": ["G1", "G2"],
            "hits_promoter": [0, 1],
            "hits_exon": [0, 0],
            "hits_intron": [0, 0],
            "hits_gene_body": [0, 0],
            "hits_terminator": [0, 0],
        }
    )
    out = analyzer._apply_csv_filters(df)
    assert out["gene_name"].tolist() == ["G2"]


def test_load_gene_list_requires_mapper_contract_columns(tmp_path):
    analyzer = EnrichmentAnalyzer(libraries=["GO_Biological_Process_2023"])
    csv_path = tmp_path / "mapper.csv"
    csv_path.write_text("gene_name,total_weight\nG1,1.0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required mapper columns"):
        analyzer.load_gene_list(csv_path, gene_column="gene_name")


def test_min_dmp_count_falls_back_to_unique_dmps_when_dmp_count_missing():
    analyzer = EnrichmentAnalyzer(libraries=["GO_Biological_Process_2023"])
    df = pd.DataFrame(
        {
            "gene_name": ["G1", "G2", "G3"],
            "unique_dmps": [1, 2, 3],
            "gene_importance": [0.1, 0.2, 0.3],
            "mean_effect_size": [0.1, 0.2, 0.3],
        }
    )
    out = analyzer._apply_csv_filters(df, min_dmp_count=2)
    assert out["gene_name"].tolist() == ["G2", "G3"]
