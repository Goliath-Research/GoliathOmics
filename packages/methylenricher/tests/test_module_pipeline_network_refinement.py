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
                "Term": ["DNA Repair", "Cell Growth", "DNA REPAIR"],
                "Genes": ["TP53;BRCA1", "EGFR;MTOR", "TP53;BRCA1"],
                "Adjusted P-value": [0.001, 0.02, 0.003],
                "P-value": [0.0005, 0.015, 0.002],
                "Odds Ratio": [5.0, 2.0, 4.6],
                "library": ["Reactome_2022", "KEGG_2021_Human", "GO_Biological_Process_2023"],
            }
        )


def test_module_pipeline_network_refinement_local_edges(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzer)
    in_file = tmp_path / "genes.csv"
    in_file.write_text("gene\nTP53\n", encoding="utf-8")
    edges_file = tmp_path / "edges.csv"
    edges_file.write_text(
        "source,target,score\nTP53,BRCA1,800\nEGFR,MTOR,700\nTP53,EGFR,450\n",
        encoding="utf-8",
    )

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        network_refinement_enabled=True,
        network_refinement_source="local_edges",
        network_refinement_local_edges_file=str(edges_file),
        network_refinement_score_threshold=400.0,
        network_refinement_community_method="connected_components",
        network_refinement_weight_in_final_score=0.35,
        network_plot="none",
    )

    assert not out_df.empty
    assert {"PPI_coherence_score", "Blended_score", "Base_score"}.issubset(set(out_df.columns))
    assert (tmp_path / "ppi_network_edges.csv").exists()
    nm = pd.read_csv(tmp_path / "ppi_node_metrics.csv")
    assert "combined_hub_score" in nm.columns
    assert "methylation_weight_norm" in nm.columns
    assert (tmp_path / "ppi_hubs.csv").exists()
    mc = pd.read_csv(tmp_path / "ppi_module_coherence.csv")
    assert "ppi_mean_combined_hub_score" in mc.columns


def test_module_pipeline_network_refinement_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzer)
    in_file = tmp_path / "genes.csv"
    in_file.write_text("gene\nTP53\n", encoding="utf-8")

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        network_refinement_enabled=True,
        network_refinement_source="local_edges",
        network_refinement_local_edges_file=str(tmp_path / "missing.csv"),
        network_plot="none",
    )

    assert not out_df.empty
    assert "PPI_coherence_score" in out_df.columns
    # Failure path should skip PPI artifacts and continue baseline ranking.
    assert not (tmp_path / "ppi_module_coherence.csv").exists()


def test_module_pipeline_passes_ppi_dataset_to_dash(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module_pipeline, "EnrichmentAnalyzer", _FakeAnalyzer)
    captured = {}

    def _capture_write_network_plots(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(module_pipeline.module_network_plot, "write_network_plots", _capture_write_network_plots)

    in_file = tmp_path / "genes.csv"
    in_file.write_text("gene\nTP53\n", encoding="utf-8")
    edges_file = tmp_path / "edges.csv"
    edges_file.write_text(
        "source,target,score\nTP53,BRCA1,800\nEGFR,MTOR,700\nTP53,EGFR,450\n",
        encoding="utf-8",
    )

    out_df = module_pipeline.run_module_pipeline(
        input_path=in_file,
        output_dir=tmp_path,
        network_refinement_enabled=True,
        network_refinement_source="local_edges",
        network_refinement_local_edges_file=str(edges_file),
        network_plot="dash",
    )

    assert not out_df.empty
    assert captured.get("network_plot") == "dash"
    assert captured.get("ppi_elements")
    assert captured.get("ppi_stylesheet")
