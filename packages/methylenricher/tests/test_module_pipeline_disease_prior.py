from pathlib import Path

import pandas as pd

from methyl_enricher import module_pipeline


class _FakeAnalyzer:
    def __init__(self, libraries=None, organism="Human", cutoff=0.05):
        self.libraries = libraries or []
        self.organism = organism
        self.cutoff = cutoff

    def load_gene_list_with_weights(self, *_args, **_kwargs):
        genes = ["TP53", "BRCA1", "EGFR", "MTOR"]
        weights = {"TP53": 0.9, "BRCA1": 0.8, "EGFR": 0.7, "MTOR": 0.6}
        return genes, weights

    def run_enrichment(self, genes, _output_dir, **_kwargs):
        assert genes
        return pd.DataFrame(
            {
                "Term": ["DNA Repair", "Cell Growth", "DNA REPAIR", "Immune response"],
                "Genes": ["TP53;BRCA1", "EGFR;MTOR", "TP53;BRCA1", "TP53;EGFR"],
                "Adjusted P-value": [0.001, 0.02, 0.003, 0.01],
                "P-value": [0.0005, 0.015, 0.002, 0.008],
                "Odds Ratio": [5.0, 2.0, 4.6, 3.4],
                "library": [
                    "Reactome_2022",
                    "KEGG_2021_Human",
                    "GO_Biological_Process_2023",
                    "MSigDB_Hallmark_2020",
                ],
            }
        )


def test_modules_include_disease_columns_when_prior_exists(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzer)
    in_file = tmp_path / "mapper_with_disease.csv"
    in_file.write_text(
        (
            "gene_name,gene_importance,mean_effect_size,unique_dmps,disease_associated,disease_score,disease_evidence_level\n"
            "TP53,0.9,0.9,3,True,0.82,high\n"
            "BRCA1,0.8,0.8,2,True,0.71,medium\n"
            "EGFR,0.7,0.7,2,False,0.10,low\n"
            "MTOR,0.6,0.6,1,False,0.05,low\n"
        ),
        encoding="utf-8",
    )

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        gene_column="gene_name",
        network_plot="none",
    )

    assert not out_df.empty
    assert "Disease_relevance_score" in out_df.columns
    assert "Disease_relevance_tier" in out_df.columns
    assert out_df["Disease_relevance_score"].notna().any()


def test_modules_omit_disease_columns_when_no_disease_metadata(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzer)
    in_file = tmp_path / "mapper_without_disease.csv"
    in_file.write_text(
        (
            "gene_name,gene_importance,mean_effect_size,unique_dmps\n"
            "TP53,0.9,0.9,3\n"
            "BRCA1,0.8,0.8,2\n"
            "EGFR,0.7,0.7,2\n"
            "MTOR,0.6,0.6,1\n"
        ),
        encoding="utf-8",
    )

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        gene_column="gene_name",
        network_plot="none",
    )

    assert not out_df.empty
    assert "Disease_relevance_score" not in out_df.columns
    assert "Disease_relevance_tier" not in out_df.columns


def test_modules_omit_disease_columns_when_prior_filters_to_empty(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzer)
    in_file = tmp_path / "mapper_disease_empty_prior.csv"
    in_file.write_text(
        (
            "gene_name,gene_importance,mean_effect_size,unique_dmps,disease_associated,disease_score,disease_evidence_level\n"
            "TP53,0.9,0.9,3,True,0.12,low\n"
            "BRCA1,0.8,0.8,2,True,0.11,low\n"
            "EGFR,0.7,0.7,2,False,0.02,low\n"
            "MTOR,0.6,0.6,1,False,0.01,low\n"
        ),
        encoding="utf-8",
    )

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        gene_column="gene_name",
        min_disease_score=0.95,
        network_plot="none",
    )

    assert not out_df.empty
    assert "Disease_relevance_score" not in out_df.columns
    assert "Disease_relevance_tier" not in out_df.columns


def test_modules_export_activity_columns(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzer)
    in_file = tmp_path / "mapper_for_activity.csv"
    in_file.write_text(
        (
            "gene_name,gene_importance,mean_effect_size,unique_dmps\n"
            "TP53,0.9,1.3,3\n"
            "BRCA1,0.8,1.0,2\n"
            "EGFR,0.7,0.4,2\n"
            "MTOR,0.6,0.2,1\n"
        ),
        encoding="utf-8",
    )

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        gene_column="gene_name",
        network_plot="none",
    )

    assert not out_df.empty
    expected_cols = {
        "Activity_combined_score_mean",
        "Activity_log10q_mean",
        "Activity_overlap_effect_mean",
        "Activity_gene_effect_mean",
    }
    assert expected_cols.issubset(set(out_df.columns))
    assert pd.to_numeric(out_df["Activity_combined_score_mean"], errors="coerce").fillna(0.0).gt(0).any()
    assert pd.to_numeric(out_df["Activity_gene_effect_mean"], errors="coerce").fillna(0.0).gt(0).any()

    detailed_df = pd.read_csv(tmp_path / "modules_ranked_detailed.csv")
    assert expected_cols.issubset(set(detailed_df.columns))
