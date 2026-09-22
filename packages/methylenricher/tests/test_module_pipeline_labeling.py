from pathlib import Path

import pandas as pd

from methyl_enricher import module_pipeline


class _FakeAnalyzerLabeling:
    def __init__(self, libraries=None, organism="Human", cutoff=0.05):
        self.libraries = libraries or []
        self.organism = organism
        self.cutoff = cutoff

    def load_gene_list_with_weights(self, *_args, **_kwargs):
        genes = ["TP53", "EGFR", "AKT1", "MTOR"]
        weights = {"TP53": 0.9, "EGFR": 0.85, "AKT1": 0.8, "MTOR": 0.75}
        return genes, weights

    def run_enrichment(self, genes, _output_dir, **_kwargs):
        assert genes
        return pd.DataFrame(
            {
                "Term": [
                    "PI3K-Akt signaling pathway",
                    "EGFR signaling pathway",
                    "metformin hydrochloride",
                ],
                "Genes": [
                    "TP53;EGFR;AKT1;MTOR",
                    "TP53;EGFR;AKT1;MTOR",
                    "TP53;EGFR;AKT1;MTOR",
                ],
                "Adjusted P-value": [0.0005, 0.001, 0.002],
                "P-value": [0.0001, 0.0002, 0.0005],
                "Odds Ratio": [8.0, 7.5, 6.0],
                "library": [
                    "KEGG_2021_Human",
                    "Reactome_2022",
                    "DSigDB",
                ],
            }
        )


def test_dual_label_mode_keeps_canonical_primary(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzerLabeling)
    in_file = tmp_path / "genes.csv"
    in_file.write_text("gene\nTP53\n", encoding="utf-8")

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        module_label_mode="dual_label",
        network_plot="none",
    )

    assert not out_df.empty
    assert {
        "Module_primary",
        "Module_variant_family",
        "Module_supporting_perturbation",
        "Module_display",
    }.issubset(set(out_df.columns))
    primary = str(out_df.iloc[0]["Module_primary"])
    variant_family = str(out_df.iloc[0]["Module_variant_family"])
    supporting = str(out_df.iloc[0]["Module_supporting_perturbation"])
    display = str(out_df.iloc[0]["Module_display"])
    assert primary == "PI3K / growth-factor signaling"
    assert variant_family.startswith("PI3K / growth-factor signaling | ")
    assert "perturbation_evidence" in variant_family
    assert "metformin hydrochloride" in supporting.lower()
    assert "|" in display
    assert "metformin hydrochloride" not in primary.lower()


def test_canonical_only_mode_does_not_append_subtitle(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzerLabeling)
    in_file = tmp_path / "genes.csv"
    in_file.write_text("gene\nTP53\n", encoding="utf-8")

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        module_label_mode="canonical_only",
        network_plot="none",
    )

    assert not out_df.empty
    primary = str(out_df.iloc[0]["Module_primary"])
    display = str(out_df.iloc[0]["Module_display"])
    assert display == primary
    assert "|" not in display


def test_module_variant_family_is_deterministic_from_qvalue_order():
    merged_df = pd.DataFrame(
        {
            "Term": ["A", "B", "C"],
            "_library_category": ["perturbation", "perturbation", "canonical"],
            "_library_name": ["LINCS", "DSigDB", "KEGG_2021_Human"],
            "Adjusted P-value": [0.05, 0.001, 1e-6],
        }
    )
    family = module_pipeline._module_variant_family(  # noqa: SLF001 - intentional unit test of helper
        "PI3K / growth-factor signaling",
        pathways=[module_pipeline.canonical_pathway_key(t) for t in ["A", "B", "C"]],
        merged_df=merged_df,
        top_k=2,
    )
    assert family == "PI3K / growth-factor signaling | DSigDB + LINCS"
