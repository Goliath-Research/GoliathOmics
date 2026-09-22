from __future__ import annotations

from pathlib import Path

from methyl_enricher.enricher import EnrichmentAnalyzer
from methyl_enricher.enricher_completeness import RetryPolicy, read_task_status
from methyl_enricher.ensure_complete import run_comparison_enrichment


def _mapper_csv(path: Path) -> None:
    path.write_text(
        "gene_name,gene_importance,mean_effect_size,unique_dmps,disease_associated,"
        "disease_evidence_level,disease_score\n"
        "GENE1,1.0,0.5,1,True,medium,0.8\n"
        "GENE2,0.9,0.4,1,True,medium,0.7\n"
        "GENE3,0.8,0.3,1,True,medium,0.6\n",
        encoding="utf-8",
    )


def test_sort_by_total_weight_maps_to_gene_importance(tmp_path, capsys):
    analyzer = EnrichmentAnalyzer(libraries=["GO_Biological_Process_2023"])
    csv_path = tmp_path / "mapper.csv"
    csv_path.write_text(
        "gene_name,gene_importance,mean_effect_size,unique_dmps\n"
        "G_LOW,0.1,0.1,2\n"
        "G_HIGH,0.9,0.9,2\n",
        encoding="utf-8",
    )

    genes = analyzer.load_gene_list(csv_path, sort_by="total_weight", sort_ascending=False)

    assert genes == ["G_HIGH", "G_LOW"]
    assert "mapped to 'gene_importance'" in capsys.readouterr().out


def test_ensure_complete_skips_comparison_when_filters_remove_all_genes(tmp_path):
    inp = tmp_path / "all-gene_name-combined.csv"
    out = tmp_path / "enricher"
    _mapper_csv(inp)

    analyzer = EnrichmentAnalyzer(
        libraries=["GO_Biological_Process_2023"],
        organism="Human",
        cutoff=0.05,
    )

    def _load_genes(path: Path):
        return analyzer.load_gene_list(
            path,
            disease_only=True,
            min_disease_evidence_level="medium",
            min_dmp_count=2,
            sort_by="total_weight",
        )

    report, results = run_comparison_enrichment(
        inp,
        out,
        "PCa",
        libraries=["GO_Biological_Process_2023"],
        organism="Human",
        cutoff=0.05,
        policy=RetryPolicy(max_retries=0, inter_library_delay_seconds=0),
        load_genes_fn=_load_genes,
    )

    assert results == []
    assert report.complete is False
    assert report.present_libraries == []
    status = read_task_status(out)
    assert status is not None
    assert status["status"] == "failed"
    assert status["comparison_label"] == "PCa"
