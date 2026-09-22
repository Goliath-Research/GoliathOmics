from __future__ import annotations

import pandas as pd

from methyl_enricher.enricher import EnrichmentAnalyzer


def test_skip_gene_q_filter_when_no_finite_values():
    analyzer = EnrichmentAnalyzer()
    df = pd.DataFrame(
        {
            "gene_name": ["A", "B", "C"],
            "gene_q_value": [None, float("nan"), "not-a-number"],
        }
    )
    out = analyzer._apply_csv_filters(df, max_gene_q_value=0.05)
    assert len(out) == 3

