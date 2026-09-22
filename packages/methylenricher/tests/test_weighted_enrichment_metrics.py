import pandas as pd

from methyl_enricher.enricher import EnrichmentAnalyzer


def test_attach_weighted_overlap_metrics_uses_gene_weights():
    analyzer = EnrichmentAnalyzer(libraries=["KEGG_2021_Human"])
    merged = pd.DataFrame(
        {
            "Term": ["Pathway A", "Pathway B"],
            "Genes": ["TP53;EGFR", "MTOR;UNKNOWN"],
            "Adjusted P-value": [0.001, 0.02],
            "library": ["KEGG_2021_Human", "KEGG_2021_Human"],
        }
    )
    weights = {"TP53": 0.9, "EGFR": 0.7, "MTOR": 0.5}

    out = analyzer._attach_weighted_overlap_metrics(merged, weights)  # noqa: SLF001 - helper unit test

    assert "overlap_weight_mean" in out.columns
    assert "overlap_weight_abs_mean" in out.columns
    assert "overlap_weight_sum" in out.columns
    assert out.loc[0, "overlap_weight_mean"] == 0.8
    assert out.loc[0, "overlap_weight_abs_mean"] == 0.8
    assert out.loc[0, "overlap_weight_sum"] == 1.6
    assert out.loc[0, "overlap_weight_n"] == 2
    assert out.loc[1, "overlap_weight_mean"] == 0.5
    assert out.loc[1, "overlap_weight_n"] == 1
